"""GET /feed エンドポイント。"""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends

from api.dependencies import get_firestore_client, get_user_id
from api.schemas import ArticleResponse, FeedResponse
from shared.firestore_client import FirestoreClient
from shared.models import Article

router = APIRouter()

# iOS クライアントに返すフィードの最大記事数。
# Recommendation に含まれる上位 N 件のみを返し、ネットワーク負荷を抑える。
_FEED_MAX_ARTICLES = 50

# レコメンドがこの件数以上ある場合のみ、レコメンド上位を返す。
# 下回る場合（コールドスタート等でレコメンドが疎なとき）は全記事フォールバックし、
# フィードが空・極端に少ない状態になるのを避ける。
_RECOMMENDATION_MIN_ARTICLES = 100

# レコメンドに含まれない記事のデフォルトスコア。フォールバックで取得した記事など、
# スコア未確定のものに付与する中立値。
_DEFAULT_SCORE = 0.5


def _filter_unread_ids(candidate_ids: list[str], read_ids: list[str], dismissed_ids: list[str]) -> list[str]:
    """未読フィルタ: read_ids と dismissed_ids の和集合を除外する（純粋関数）。

    Args:
        candidate_ids: フィルタ対象の記事ID リスト。
        read_ids: 既読の記事ID リスト。
        dismissed_ids: 却下された記事ID リスト。

    Returns:
        read_ids ∪ dismissed_ids に含まれない候補ID のリスト（順序は保持）。
    """
    excluded = set(read_ids) | set(dismissed_ids)
    return [aid for aid in candidate_ids if aid not in excluded]


def _to_response(article: Article, score: float, is_read: bool = False) -> ArticleResponse:
    """Article とスコアから API レスポンスモデルを生成する。

    通常経路・フォールバック経路で同一の変換を使うため共通化する。

    Args:
        article: 記事モデル。
        score: 記事スコア。
        is_read: 既読フラグ（既読フィルタで付与される）。
    """
    return ArticleResponse(
        id=article.id,
        title=article.title,
        url=article.url,
        source=article.source,
        score=score,
        published_at=article.published_at.isoformat(),
        is_read=is_read,
    )


@router.get("/feed", response_model=FeedResponse)
def get_feed(
    filter: Literal["all", "unread"] = "all",
    user_id: str = Depends(get_user_id),
    db: FirestoreClient = Depends(get_firestore_client),
):
    """GET /feed エンドポイント。

    Args:
        filter: フィルタモード。"all"（全記事・既定）または "unread"（未読のみ）。
            不正値は FastAPI が 422（標準のバリデーションエラー形式）を返す。

    Returns:
        FeedResponse（記事リスト＋本日の日付）。
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # prefs は1回だけ取得。read/dismissed は O(1) 判定のため set 化（集約）。
    prefs = db.get_user_prefs(user_id)
    read_ids = set(prefs.read_article_ids)
    dismissed_ids = set(prefs.dismissed_article_ids)

    rec = db.get_recommendation(user_id, today)
    scores_by_id = {a.article_id: a.score for a in rec.articles} if rec else {}

    if rec and len(rec.articles) >= _RECOMMENDATION_MIN_ARTICLES:
        # 通常経路: レコメンド上位 _FEED_MAX_ARTICLES 件を返す。
        article_ids = [a.article_id for a in rec.articles][:_FEED_MAX_ARTICLES]

        # filter="unread" のとき、read_ids ∪ dismissed_ids を除外
        if filter == "unread":
            article_ids = _filter_unread_ids(article_ids, read_ids, dismissed_ids)

        article_responses = [
            _to_response(
                article,
                scores_by_id.get(article.id, _DEFAULT_SCORE),
                is_read=article.id in read_ids,
            )
            for article in (db.get_article(aid) for aid in article_ids)
            if article
        ]
    else:
        # フォールバック経路: レコメンドが基準件数未満（None / 空を含む）のときは
        # 全記事を返す。filter="all"なら dismissed のみ除外（後方互換）。
        # filter="unread" なら read ∪ dismissed を除外。
        articles = db.get_recent_articles()

        if filter == "unread":
            # 未読フィルタ: read ∪ dismissed を除外
            filtered_articles = [
                article for article in articles
                if article.id not in read_ids and article.id not in dismissed_ids
            ]
        else:
            # filter="all"（既定）: dismissed のみ除外（後方互換）
            filtered_articles = [
                article for article in articles
                if article.id not in dismissed_ids
            ]

        article_responses = [
            _to_response(
                article,
                scores_by_id.get(article.id, _DEFAULT_SCORE),
                is_read=article.id in read_ids,
            )
            for article in filtered_articles
        ]

    return FeedResponse(articles=article_responses, date=today)
