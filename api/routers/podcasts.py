"""GET /podcasts, GET /podcasts/{id}, PATCH /podcasts/{id}/position, GET /podcasts/storage/usage, POST /podcasts/storage/cleanup エンドポイント。"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from api.dependencies import (
    get_firestore_client,
    get_storage_client,
    get_user_id,
    get_current_user,
    get_audit_logger,
    get_client_ip,
)
from api.schemas import (
    PodcastListResponse,
    PodcastResponse,
    UpdatePlaybackPositionRequest,
    StorageUsageResponse,
    StorageUsageItem,
    StorageCleanupRequest,
    StorageCleanupResponse,
)
from api.storage_cleanup import is_blob_deletable, select_podcasts_to_delete
from api.audit import AuditLogger
from shared.firestore_client import FirestoreClient
from shared.storage_client import StorageClient
from shared.models import Session

router = APIRouter()


@router.get("/podcasts/storage/usage", response_model=StorageUsageResponse)
def get_storage_usage(
    user_id: str = Depends(get_user_id),
    db: FirestoreClient = Depends(get_firestore_client),
    storage: StorageClient = Depends(get_storage_client),
):
    """所有 Podcast の総ストレージ使用量を取得する（read-only・監査記録なし）。

    limit=1000 で最大 1000 件まで取得し、各 audio_url の blob サイズを合計する。
    blob 不在・エラー時は 0 を加算（get_blob_size が安全）。
    """
    podcasts = db.get_podcasts_for_user(user_id, limit=1000)
    items = []
    total_bytes = 0

    for podcast in podcasts:
        # blob サイズを取得（不在・エラー時は 0）
        size_bytes = storage.get_blob_size(podcast.audio_url)
        total_bytes += size_bytes

        # item を構築
        items.append(
            StorageUsageItem(
                id=podcast.id,
                type=podcast.type,
                size_bytes=size_bytes,
                created_at=podcast.created_at.isoformat(),
            )
        )

    return StorageUsageResponse(
        total_bytes=total_bytes,
        podcast_count=len(podcasts),
        items=items,
    )


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
            # processing 行は audio_url 未確定（空）のため署名 URL 変換をスキップ（空 blob 署名の無駄/失敗を防ぐ）。
            PodcastResponse.from_podcast(
                p, audio_url=storage.generate_audio_url(p.audio_url) if p.audio_url else ""
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
    # processing 行は audio_url 未確定（空）のため署名 URL 変換をスキップ（空 blob 署名の無駄/失敗を防ぐ）。
    return PodcastResponse.from_podcast(
        podcast, audio_url=storage.generate_audio_url(podcast.audio_url) if podcast.audio_url else ""
    )


@router.post("/podcasts/storage/cleanup", response_model=StorageCleanupResponse)
def cleanup_storage(
    request: StorageCleanupRequest,
    http_request: Request,
    current_user: Session = Depends(get_current_user),
    db: FirestoreClient = Depends(get_firestore_client),
    storage: StorageClient = Depends(get_storage_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """所有 Podcast のストレージをクリーンアップする（digest per-user blob のみ削除）。

    確定設計に従い:
    - single/legacy の blob は決して削除しない（shared cache は GC 30 日）
    - Podcast ドキュメントは type 問わず削除
    - blob 削除は best-effort（失敗時も握り潰して続行）
    - doc 削除は確実化（doc 残り blob なしの壊れ状態を避ける）
    - 集計（deleted_blob_count / freed_bytes）は **実際に削除成功した blob のみ**計上
    """
    user_id = current_user.user_id
    now = datetime.now(timezone.utc)

    # podcasts の古い行から削除対象を選別
    podcasts = db.get_podcasts_for_user(user_id, limit=1000)
    targets = select_podcasts_to_delete(podcasts, request.older_than_days, now)

    deleted_podcast_count = 0
    deleted_blob_count = 0
    freed_bytes = 0

    for target in targets:
        # digest の per-user blob のみ削除（single/legacy の共有 blob は触らない）
        if is_blob_deletable(target):
            # blob size を計測してから削除（best-effort）。実際に削除成功した分だけ計上し、
            # 失敗（権限不足等）を「解放済み」と誤報告しない。
            blob_size = storage.get_blob_size(target.audio_url)
            if storage.delete_blob(target.audio_url):
                freed_bytes += blob_size
                deleted_blob_count += 1

        # doc は type 問わず削除（確実化）
        db.delete_podcast(target.id)
        deleted_podcast_count += 1

    # 監査記録（details に id/blob_path/article_id は含めない）
    audit_logger.record(
        action="storage_cleanup",
        actor=current_user,
        ip=get_client_ip(http_request),
        details={
            "older_than_days": request.older_than_days,
            "deleted_podcast_count": deleted_podcast_count,
            "deleted_blob_count": deleted_blob_count,
            "freed_bytes": freed_bytes,
        },
    )

    return StorageCleanupResponse(
        deleted_podcast_count=deleted_podcast_count,
        deleted_blob_count=deleted_blob_count,
        freed_bytes=freed_bytes,
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

    # processing 行は audio_url 未確定（空）のため署名 URL 変換をスキップ（空 blob 署名の無駄/失敗を防ぐ）。
    return PodcastResponse.from_podcast(
        updated, audio_url=storage.generate_audio_url(updated.audio_url) if updated.audio_url else ""
    )
