"""GET /podcasts, GET /podcasts/{id}, PATCH /podcasts/{id}/position エンドポイント。"""
from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_firestore_client, get_storage_client, get_user_id
from api.schemas import (
    PodcastListResponse,
    PodcastResponse,
    UpdatePlaybackPositionRequest,
)
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


@router.patch("/podcasts/{podcast_id}/position", response_model=PodcastResponse)
def patch_playback_position(
    podcast_id: str,
    request: UpdatePlaybackPositionRequest,
    user_id: str = Depends(get_user_id),
    db: FirestoreClient = Depends(get_firestore_client),
    storage: StorageClient = Depends(get_storage_client),
):
    """再生位置を更新する。duration_seconds に clamp する（クライアント申告を信用しない）。"""
    podcast = db.get_podcast(podcast_id)
    # 所有権チェック: podcast is None or podcast.user_id != user_id → 404
    # save_podcast より前に置く（不一致時は save_podcast を呼ばない）
    if not podcast or podcast.user_id != user_id:
        raise HTTPException(status_code=404, detail="Podcast not found")

    # clamp: position_seconds を duration_seconds 以下に制限
    clamped = min(request.position_seconds, float(podcast.duration_seconds))

    # 永続化: model_copy で不変更新
    updated = podcast.model_copy(update={"playback_position_seconds": clamped})
    db.save_podcast(updated)

    return PodcastResponse.from_podcast(
        updated, audio_url=storage.generate_audio_url(updated.audio_url)
    )
