"""GET /feed エンドポイント。"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from api.dependencies import get_firestore_client, get_user_id
from api.schemas import ArticleResponse, FeedResponse
from shared.firestore_client import FirestoreClient

router = APIRouter()

# iOS クライアントに返すフィードの最大記事数。
# Recommendation に含まれる上位 N 件のみを返し、ネットワーク負荷を抑える。
_FEED_MAX_ARTICLES = 50


@router.get("/feed", response_model=FeedResponse)
def get_feed(
    user_id: str = Depends(get_user_id),
    db: FirestoreClient = Depends(get_firestore_client),
):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    rec = db.get_recommendation(user_id, today)
    if not rec or not rec.articles:
        return FeedResponse(articles=[], date=today)

    article_ids = [a.article_id for a in rec.articles]
    scores_by_id = {a.article_id: a.score for a in rec.articles}

    article_responses = []
    for article_id in article_ids[:_FEED_MAX_ARTICLES]:
        article = db.get_article(article_id)
        if article:
            article_responses.append(
                ArticleResponse(
                    id=article.id,
                    title=article.title,
                    url=article.url,
                    source=article.source,
                    score=scores_by_id.get(article.id, 0.5),
                    published_at=article.published_at.isoformat(),
                )
            )

    return FeedResponse(articles=article_responses, date=today)
