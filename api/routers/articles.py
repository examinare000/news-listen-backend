"""POST /articles/{id}/star, /articles/{id}/dismiss エンドポイント。"""
from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_firestore_client, get_user_id
from api.schemas import ActionResponse
from shared.firestore_client import FirestoreClient

router = APIRouter()


@router.post("/articles/{article_id}/star", response_model=ActionResponse)
def star_article(
    article_id: str,
    user_id: str = Depends(get_user_id),
    db: FirestoreClient = Depends(get_firestore_client),
):
    if not db.article_exists(article_id):
        raise HTTPException(status_code=404, detail="Article not found")

    db.add_starred_article(user_id, article_id)
    return ActionResponse(status="starred", article_id=article_id)


@router.post("/articles/{article_id}/dismiss", response_model=ActionResponse)
def dismiss_article(
    article_id: str,
    user_id: str = Depends(get_user_id),
    db: FirestoreClient = Depends(get_firestore_client),
):
    if not db.article_exists(article_id):
        raise HTTPException(status_code=404, detail="Article not found")

    db.add_dismissed_article(user_id, article_id)
    return ActionResponse(status="dismissed", article_id=article_id)
