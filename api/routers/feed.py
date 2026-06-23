"""GET /feed エンドポイント。"""
from datetime import datetime, timezone

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


def _to_response(article: Article, score: float) -> ArticleResponse:
    """Article とスコアから API レスポンスモデルを生成する。

    通常経路・フォールバック経路で同一の変換を使うため共通化する。
    """
    return ArticleResponse(
        id=article.id,
        title=article.title,
        url=article.url,
        source=article.source,
        score=score,
        published_at=article.published_at.isoformat(),
    )


@router.get("/feed", response_model=FeedResponse)
def get_feed(
    user_id: str = Depends(get_user_id),
    db: FirestoreClient = Depends(get_firestore_client),
):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    rec = db.get_recommendation(user_id, today)
    scores_by_id = {a.article_id: a.score for a in rec.articles} if rec else {}

    if rec and len(rec.articles) >= _RECOMMENDATION_MIN_ARTICLES:
        # 通常経路: レコメンド上位 _FEED_MAX_ARTICLES 件を返す。
        article_ids = [a.article_id for a in rec.articles][:_FEED_MAX_ARTICLES]
        article_responses = [
            _to_response(article, scores_by_id.get(article.id, _DEFAULT_SCORE))
            for article in (db.get_article(aid) for aid in article_ids)
            if article
        ]
    else:
        # フォールバック経路: レコメンドが基準件数未満（None / 空を含む）のときは
        # 全記事を返す。dismiss 済みの記事は除外し、レコメンドに含まれる記事は
        # そのスコアを引き継ぐ（それ以外は中立スコア）。
        prefs = db.get_user_prefs(user_id)
        dismissed_ids = set(prefs.dismissed_article_ids)
        article_responses = [
            _to_response(article, scores_by_id.get(article.id, _DEFAULT_SCORE))
            for article in db.get_recent_articles()
            if article.id not in dismissed_ids
        ]

    return FeedResponse(articles=article_responses, date=today)
