"""POST /articles/{id}/star, /articles/{id}/dismiss エンドポイント＆検索。"""
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Query

from api.dependencies import (
    get_firestore_client,
    get_job_trigger,
    get_current_user,
    get_audit_logger,
    get_client_ip,
    get_user_id,
)
from api.ratelimit import rate_limit
from api.schemas import ActionResponse, ArticleSearchResponse
from api.audit import AuditLogger
from shared.firestore_client import FirestoreClient
from shared.models import DEFAULT_PODCAST_LANGUAGE, Article, Session

router = APIRouter()


def _search_articles(
    articles: list[Article],
    query: str,
    read_ids: set[str],
    dismissed_ids: set[str],
    filter: Literal["all", "unread"] = "all",
) -> list[Article]:
    """記事リストから query に部分一致する記事をフィルタして返す純粋関数。

    Args:
        articles: 検索対象の記事リスト（既に published_at DESC で整序されている想定）。
        query: 検索キーワード。title / content の部分一致で検索（大文字小文字不問）。
        read_ids: 既読記事 ID の集合。
        dismissed_ids: 却下記事 ID の集合。
        filter: フィルタモード。"all"（既定）で dismissed のみ除外。"unread" で read ∪ dismissed を除外。

    Returns:
        フィルタされた記事リスト（順序は入力値を保持）。
    """
    q_lower = query.strip().lower()
    # WHY: 空白のみクエリは strip 後 "" になり、`"" in s` が常に True で全件マッチして
    # しまう（Query(min_length=1) は空白を弾けない）。空クエリは結果なしとして早期 return。
    if not q_lower:
        return []
    excluded = (read_ids | dismissed_ids) if filter == "unread" else dismissed_ids
    return [
        a for a in articles
        if a.id not in excluded and (q_lower in a.title.lower() or q_lower in a.content.lower())
    ]


@router.get("/articles/search", response_model=ArticleSearchResponse)
def search_articles(
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    filter: Literal["all", "unread"] = "all",
    user_id: str = Depends(get_user_id),
    db: FirestoreClient = Depends(get_firestore_client),
):
    """GET /articles/search エンドポイント。

    title / content から query に部分一致する記事を返す。
    認証必須。dismissed / read 記事は filter パラメータで制御。

    Args:
        q: 検索キーワード（必須・1～100文字）。
        limit: 1ページあたりの件数（既定20・1～200）。
        offset: スキップ件数（既定0・0以上）。
        filter: "all"（既定・dismissed のみ除外）/ "unread"（read ∪ dismissed を除外）。
        user_id: 認証ユーザー ID。
        db: Firestore クライアント。

    Returns:
        ArticleSearchResponse（articles + total_count）。
    """

    # prefs から read_ids / dismissed_ids を取得し、set 化（O(1) 判定用）。
    prefs = db.get_user_prefs(user_id)
    read_ids = set(prefs.read_article_ids)
    dismissed_ids = set(prefs.dismissed_article_ids)

    # 最近記事 200 件（アプリ層フィルタの対象）を取得。
    articles = db.get_recent_articles(limit=200)

    # _search_articles で純粋に検索・フィルタ。
    matched = _search_articles(articles, q, read_ids, dismissed_ids, filter)
    total_count = len(matched)

    # offset / limit でページング（total_count はページング前の全件）。
    page = matched[offset : offset + limit]

    # ArticleResponse に変換。is_read は read_ids の有無で判定。
    # score は簡潔のため固定 0.5（recommendation の有無を反映しない）。
    from api.routers.feed import _to_response
    article_responses = [
        _to_response(article, score=0.5, is_read=article.id in read_ids)
        for article in page
    ]

    return ArticleSearchResponse(articles=article_responses, total_count=total_count)


@router.post("/articles/{article_id}/star", status_code=202, response_model=ActionResponse)
def star_article(
    article_id: str,
    http_request: Request,
    background_tasks: BackgroundTasks,
    current: Session = Depends(get_current_user),
    db: FirestoreClient = Depends(get_firestore_client),
    job_trigger=Depends(get_job_trigger),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    _rl: None = Depends(rate_limit("star")),
):
    user_id = current.user_id
    if not db.article_exists(article_id):
        raise HTTPException(status_code=404, detail="Article not found")

    db.add_starred_article(user_id, article_id)

    # 202=PRD §7 受理（生成は非同期）。star 受付時に processing 行を原子的に確保し
    # 「生成中」をクライアントへ可視化する。難易度は prefs.default_difficulty
    # （generator と一致させ重複行を防ぐ）。
    prefs = db.get_user_prefs(user_id)
    difficulty = prefs.default_difficulty
    db.try_acquire_user_podcast(user_id, article_id, difficulty, DEFAULT_PODCAST_LANGUAGE)

    # 監査ログ記録（成功後）
    audit_logger.record(
        action="article_star",
        actor=current,
        ip=get_client_ip(http_request),
        details={"article_id": article_id},
    )

    # スターは正のシグナル: レコメンド再計算と当該記事の Podcast 生成を自動起動する。
    # 重い処理（TTS で数分）はジョブ側コンテナで非同期実行されるため、ここでは起動だけを
    # レスポンス送出後の BackgroundTask に委ね、ユーザー操作の応答性を保つ。
    background_tasks.add_task(job_trigger.trigger, "recommendation", user_id)
    background_tasks.add_task(job_trigger.trigger, "podcast-generator", user_id)

    return ActionResponse(status="processing", article_id=article_id)


@router.post("/articles/{article_id}/dismiss", response_model=ActionResponse)
def dismiss_article(
    article_id: str,
    http_request: Request,
    background_tasks: BackgroundTasks,
    current: Session = Depends(get_current_user),
    db: FirestoreClient = Depends(get_firestore_client),
    job_trigger=Depends(get_job_trigger),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    user_id = current.user_id
    if not db.article_exists(article_id):
        raise HTTPException(status_code=404, detail="Article not found")

    db.add_dismissed_article(user_id, article_id)

    # 監査ログ記録（成功後）
    audit_logger.record(
        action="article_dismiss",
        actor=current,
        ip=get_client_ip(http_request),
        details={"article_id": article_id},
    )

    # dismiss は負のシグナル: レコメンド再計算のみ起動する（Podcast は生成しない）。
    background_tasks.add_task(job_trigger.trigger, "recommendation", user_id)

    return ActionResponse(status="dismissed", article_id=article_id)


@router.post("/articles/{article_id}/mark-read", response_model=ActionResponse)
def mark_read_article(
    article_id: str,
    http_request: Request,
    current: Session = Depends(get_current_user),
    db: FirestoreClient = Depends(get_firestore_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    user_id = current.user_id
    if not db.article_exists(article_id):
        raise HTTPException(status_code=404, detail="Article not found")

    db.add_read_article(user_id, article_id)

    # 監査ログ記録（成功後）
    audit_logger.record(
        action="article_mark_read",
        actor=current,
        ip=get_client_ip(http_request),
        details={"article_id": article_id},
    )

    return ActionResponse(status="read", article_id=article_id)
