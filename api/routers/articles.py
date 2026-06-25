"""POST /articles/{id}/star, /articles/{id}/dismiss エンドポイント。"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from api.dependencies import (
    get_firestore_client,
    get_job_trigger,
    get_current_user,
    get_audit_logger,
    get_client_ip,
)
from api.ratelimit import rate_limit
from api.schemas import ActionResponse
from api.audit import AuditLogger
from shared.firestore_client import FirestoreClient
from shared.models import DEFAULT_PODCAST_LANGUAGE, Session

router = APIRouter()


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
