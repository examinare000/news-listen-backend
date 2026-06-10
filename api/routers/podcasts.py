"""GET /podcasts, GET /podcasts/{id} エンドポイント。"""
from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_firestore_client, get_storage_client, get_user_id
from api.schemas import PodcastListResponse, PodcastResponse
from shared.firestore_client import FirestoreClient
from shared.storage_client import StorageClient

router = APIRouter()


@router.get("/podcasts", response_model=PodcastListResponse)
def list_podcasts(
    user_id: str = Depends(get_user_id),
    db: FirestoreClient = Depends(get_firestore_client),
    storage: StorageClient = Depends(get_storage_client),
):
    podcasts = db.get_podcasts_for_user(user_id)
    return PodcastListResponse(
        podcasts=[
            # Firestore には GCS blob path が保存されている。
            # iOS クライアントが直接再生できる署名付き URL（有効期限 1 時間）に変換して返す。
            PodcastResponse.from_podcast(
                p, audio_url=storage.generate_audio_url(p.audio_url)
            )
            for p in podcasts
        ]
    )


@router.get("/podcasts/{podcast_id}", response_model=PodcastResponse)
def get_podcast(
    podcast_id: str,
    user_id: str = Depends(get_user_id),
    db: FirestoreClient = Depends(get_firestore_client),
    storage: StorageClient = Depends(get_storage_client),
):
    """spec-reviewer: O(1) 直接取得（全件取得後 Python フィルタではなく）。"""
    podcast = db.get_podcast(podcast_id)
    if not podcast or podcast.user_id != user_id:
        raise HTTPException(status_code=404, detail="Podcast not found")
    return PodcastResponse.from_podcast(
        podcast, audio_url=storage.generate_audio_url(podcast.audio_url)
    )
