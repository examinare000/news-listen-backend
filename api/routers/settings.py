"""GET/POST/DELETE /settings/sources エンドポイント。"""
from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_firestore_client, get_user_id
from api.schemas import RssSourceRequest, RssSourcesResponse
from shared.firestore_client import FirestoreClient
from shared.models import RssSource

router = APIRouter()


@router.get("/settings/sources", response_model=RssSourcesResponse)
def get_sources(
    user_id: str = Depends(get_user_id),
    db: FirestoreClient = Depends(get_firestore_client),
):
    prefs = db.get_user_prefs(user_id)
    return RssSourcesResponse(sources=[s.model_dump() for s in prefs.rss_sources])


@router.post("/settings/sources", response_model=RssSourcesResponse)
def add_source(
    request: RssSourceRequest,
    user_id: str = Depends(get_user_id),
    db: FirestoreClient = Depends(get_firestore_client),
):
    prefs = db.get_user_prefs(user_id)

    # HttpUrl を str に変換して比較・保存
    url_str = str(request.url)

    # 重複チェック
    if any(s.url == url_str for s in prefs.rss_sources):
        raise HTTPException(status_code=409, detail="Source URL already exists")

    updated = prefs.model_copy(
        update={
            "rss_sources": prefs.rss_sources + [
                RssSource(name=request.name, url=url_str)
            ]
        }
    )
    db.save_user_prefs(updated)
    return RssSourcesResponse(sources=[s.model_dump() for s in updated.rss_sources])


@router.delete("/settings/sources")
def remove_source(
    url: str,
    user_id: str = Depends(get_user_id),
    db: FirestoreClient = Depends(get_firestore_client),
):
    prefs = db.get_user_prefs(user_id)

    new_sources = [s for s in prefs.rss_sources if s.url != url]
    if len(new_sources) == len(prefs.rss_sources):
        raise HTTPException(status_code=404, detail="Source not found")

    updated = prefs.model_copy(update={"rss_sources": new_sources})
    db.save_user_prefs(updated)
    return RssSourcesResponse(sources=[s.model_dump() for s in updated.rss_sources])
