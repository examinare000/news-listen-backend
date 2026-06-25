"""GET /podcasts, GET /podcasts/{id} のテスト。"""
from datetime import datetime, timezone, timedelta
from shared.models import Podcast


def _make_podcast(podcast_id="pod1", duration_seconds=300):
    return Podcast(
        id=podcast_id,
        type="single",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="podcasts/pod1/toeic_900.mp3",
        japanese_intro_text="日本語イントロ",
        duration_seconds=duration_seconds,
        status="completed",
        playback_position_seconds=0.0,  # デフォルト値: 後方互換のため既存 Firestore ドキュメントに無い
        created_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
        user_id="user1",
    )


def test_list_podcasts_returns_200(api_client, mock_db):
    mock_db.get_podcasts_for_user.return_value = [_make_podcast()]

    response = api_client.get("/podcasts", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    data = response.json()
    assert len(data["podcasts"]) == 1
    assert data["podcasts"][0]["id"] == "pod1"


def test_get_podcast_by_id_returns_200(api_client, mock_db):
    """spec-reviewer: get_podcast(id) で O(1) 取得を確認する"""
    mock_db.get_podcast.return_value = _make_podcast("pod1")

    response = api_client.get("/podcasts/pod1", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    assert response.json()["id"] == "pod1"
    # get_podcast(id) が直接呼ばれること（全件取得後フィルタではない）
    mock_db.get_podcast.assert_called_once_with("pod1")


def test_get_podcast_by_id_returns_404_when_not_found(api_client, mock_db):
    mock_db.get_podcast.return_value = None

    response = api_client.get("/podcasts/missing", headers={"X-API-Key": "test-key"})
    assert response.status_code == 404


def test_get_podcast_by_id_returns_404_when_owned_by_other_user(api_client, mock_db):
    """別ユーザーが所有する Podcast ID を取得しようとした場合は 404 を返す"""
    other_users_podcast = Podcast(
        id="pod_other",
        type="single",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="podcasts/pod_other/toeic_900.mp3",
        japanese_intro_text="他のユーザーのイントロ",
        duration_seconds=300,
        status="completed",
        created_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
        user_id="other_user",  # current user is "user1"
    )
    mock_db.get_podcast.return_value = other_users_podcast

    response = api_client.get("/podcasts/pod_other", headers={"X-API-Key": "test-key"})
    assert response.status_code == 404


def test_list_podcasts_returns_signed_audio_url(api_client, mock_db, mock_storage):
    """GET /podcasts の audio_url は GCS blob path ではなく署名付き URL であること。

    blob.make_public() を廃止したことで audio_url は "podcasts/{id}/{diff}.mp3" という
    GCS blob path として Firestore に保存されている。iOS クライアントが直接再生できる
    https:// の署名付き URL を API レイヤーで生成してから返す必要がある。
    """
    mock_db.get_podcasts_for_user.return_value = [_make_podcast()]
    mock_storage.generate_audio_url.return_value = "https://storage.googleapis.com/signed"

    response = api_client.get("/podcasts", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    audio_url = response.json()["podcasts"][0]["audio_url"]
    assert audio_url.startswith("https://"), (
        f"GCS blob path が返された: {audio_url!r}。"
        "ルーターで StorageClient.generate_audio_url() を呼ぶこと。"
    )


def test_get_podcast_returns_signed_audio_url(api_client, mock_db, mock_storage):
    """GET /podcasts/{{id}} の audio_url は署名付き URL であること。"""
    mock_db.get_podcast.return_value = _make_podcast("pod1")
    mock_storage.generate_audio_url.return_value = "https://storage.googleapis.com/signed"

    response = api_client.get("/podcasts/pod1", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    audio_url = response.json()["audio_url"]
    assert audio_url.startswith("https://"), (
        f"GCS blob path が返された: {audio_url!r}。"
        "ルーターで StorageClient.generate_audio_url() を呼ぶこと。"
    )


def test_get_podcast_exposes_status(api_client, mock_db, mock_storage):
    """GET /podcasts/{id} レスポンスに status フィールドが含まれること。"""
    podcast = _make_podcast("pod1")
    podcast.status = "completed"
    mock_db.get_podcast.return_value = podcast
    mock_storage.generate_audio_url.return_value = "https://storage.googleapis.com/signed"

    response = api_client.get("/podcasts/pod1", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["status"] == "completed"


def test_get_podcast_exposes_error_message_when_failed(api_client, mock_db, mock_storage):
    """status="failed" の Podcast レスポンスに error_message が含まれること。"""
    podcast = _make_podcast("pod2")
    podcast.status = "failed"
    podcast.error_message = "Audio generation failed: connection timeout"
    mock_db.get_podcast.return_value = podcast
    mock_storage.generate_audio_url.return_value = "https://storage.googleapis.com/signed"

    response = api_client.get("/podcasts/pod2", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    data = response.json()
    assert "error_message" in data
    assert data["error_message"] == "Audio generation failed: connection timeout"


def test_list_podcasts_exposes_status(api_client, mock_db, mock_storage):
    """GET /podcasts レスポンスの各要素に status フィールドが含まれること。"""
    podcast = _make_podcast("pod1")
    podcast.status = "processing"
    mock_db.get_podcasts_for_user.return_value = [podcast]
    mock_storage.generate_audio_url.return_value = "https://storage.googleapis.com/signed"

    response = api_client.get("/podcasts", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    data = response.json()
    assert len(data["podcasts"]) == 1
    assert "status" in data["podcasts"][0]
    assert data["podcasts"][0]["status"] == "processing"


# ── 再生位置同期エンドポイント (PATCH /podcasts/{id}/position) ────


def test_patch_position_updates_and_returns_200(api_client, mock_db, mock_storage):
    """位置0.0の Podcast に PATCH /podcasts/pod1/position を実行し、レスポンス playback_position_seconds == 42.5。
    副作用: mock_db.save_podcast が playback_position_seconds=42.5 の Podcast 引数で呼ばれたこと。
    """
    podcast = _make_podcast("pod1")
    mock_db.get_podcast.return_value = podcast
    mock_storage.generate_audio_url.return_value = "https://storage.googleapis.com/signed"

    response = api_client.patch(
        "/podcasts/pod1/position",
        json={"position_seconds": 42.5},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["playback_position_seconds"] == 42.5
    # save_podcast が呼ばれたことを確認
    mock_db.save_podcast.assert_called_once()
    saved_podcast = mock_db.save_podcast.call_args[0][0]
    assert saved_podcast.playback_position_seconds == 42.5


def test_patch_position_clamps_to_duration(api_client, mock_db, mock_storage):
    """duration_seconds=300 の Podcast に position_seconds=99999 → レスポンス playback_position_seconds == 300.0"""
    podcast = _make_podcast("pod1")
    podcast.duration_seconds = 300
    mock_db.get_podcast.return_value = podcast
    mock_storage.generate_audio_url.return_value = "https://storage.googleapis.com/signed"

    response = api_client.patch(
        "/podcasts/pod1/position",
        json={"position_seconds": 99999},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["playback_position_seconds"] == 300.0


def test_patch_position_negative_returns_422(api_client, mock_db):
    """body {"position_seconds": -1} → 422"""
    podcast = _make_podcast("pod1")
    mock_db.get_podcast.return_value = podcast

    response = api_client.patch(
        "/podcasts/pod1/position",
        json={"position_seconds": -1},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 422


def test_patch_position_404_when_not_found(api_client, mock_db):
    """mock_db.get_podcast.return_value = None → 404"""
    mock_db.get_podcast.return_value = None

    response = api_client.patch(
        "/podcasts/missing/position",
        json={"position_seconds": 42.5},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 404


def test_patch_position_404_when_owned_by_other_user(api_client, mock_db):
    """podcast.user_id が "user1" 以外 → 404、かつ save_podcast が呼ばれないこと"""
    other_users_podcast = _make_podcast("pod_other")
    other_users_podcast.user_id = "other_user"
    mock_db.get_podcast.return_value = other_users_podcast

    response = api_client.patch(
        "/podcasts/pod_other/position",
        json={"position_seconds": 42.5},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 404
    mock_db.save_podcast.assert_not_called()


def test_podcast_response_exposes_playback_position(api_client, mock_db, mock_storage):
    """GET /podcasts/{id} のレスポンスに playback_position_seconds フィールドが存在すること。"""
    podcast = _make_podcast("pod1")
    mock_db.get_podcast.return_value = podcast
    mock_storage.generate_audio_url.return_value = "https://storage.googleapis.com/signed"

    response = api_client.get("/podcasts/pod1", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    data = response.json()
    assert "playback_position_seconds" in data
    # デフォルト値 0.0 が float として返ること（後方互換フィールドの契約）
    assert isinstance(data["playback_position_seconds"], float)


def test_list_podcasts_does_not_sign_url_for_processing_row(api_client, mock_db, mock_storage):
    """processing 状態の Podcast に対して storage.generate_audio_url が呼ばれないこと。

    processing 行は audio_url 未確定（空）のため署名 URL 変換をスキップ（空 blob 署名の無駄/失敗を防ぐ）。
    レスポンスの audio_url は空文字列で返り、クライアントは status で出し分ける。
    """
    processing_podcast = Podcast(
        id="pod_processing",
        type="single",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="",  # 未確定
        japanese_intro_text="",
        duration_seconds=0,
        status="processing",
        playback_position_seconds=0.0,
        created_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
        user_id="user1",
    )
    mock_db.get_podcasts_for_user.return_value = [processing_podcast]

    response = api_client.get("/podcasts", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    data = response.json()
    assert len(data["podcasts"]) == 1
    assert data["podcasts"][0]["audio_url"] == ""
    assert data["podcasts"][0]["status"] == "processing"
    # generate_audio_url が呼ばれていないこと
    mock_storage.generate_audio_url.assert_not_called()


def test_get_podcast_does_not_sign_url_for_processing_row(api_client, mock_db, mock_storage):
    """get_podcast(id) で processing 状態の Podcast を取得した際、generate_audio_url が呼ばれないこと。

    processing 行は audio_url 未確定（空）のため署名 URL 変換をスキップ。
    レスポンスの audio_url は空文字列、status は "processing"。
    """
    processing_podcast = Podcast(
        id="pod_processing",
        type="single",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="",
        japanese_intro_text="",
        duration_seconds=0,
        status="processing",
        playback_position_seconds=0.0,
        created_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
        user_id="user1",
    )
    mock_db.get_podcast.return_value = processing_podcast

    response = api_client.get("/podcasts/pod_processing", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    data = response.json()
    assert data["audio_url"] == ""
    assert data["status"] == "processing"
    # generate_audio_url が呼ばれていないこと
    mock_storage.generate_audio_url.assert_not_called()


# ---------- T5: GET /podcasts/storage/usage ----------


def test_get_storage_usage_returns_usage_summary(api_client_with_auth, mock_db, mock_storage, mock_audit):
    """GET /podcasts/storage/usage が total_bytes / podcast_count / items を返すこと。"""
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    pod1 = Podcast(
        id="pod1",
        type="digest",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="podcasts/user1/digest1.mp3",
        japanese_intro_text="ダイジェスト1",
        duration_seconds=300,
        status="completed",
        created_at=now - timedelta(days=5),
        user_id="user1",
    )
    pod2 = Podcast(
        id="pod2",
        type="single",
        article_ids=["art2"],
        difficulty="toeic_900",
        audio_url="podcasts/cache/art2__toeic_900__ja-en.mp3",
        japanese_intro_text="イントロ",
        duration_seconds=200,
        status="completed",
        created_at=now - timedelta(days=1),
        user_id="user1",
    )

    mock_db.get_podcasts_for_user.return_value = [pod1, pod2]
    mock_storage.get_blob_size.side_effect = [150000, 100000]  # pod1, pod2

    response = api_client_with_auth.get("/podcasts/storage/usage", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    data = response.json()
    assert data["total_bytes"] == 250000
    assert data["podcast_count"] == 2
    assert len(data["items"]) == 2
    # 監査ログが記録されないこと（read-only）
    mock_audit.record.assert_not_called()


def test_get_storage_usage_calculates_sizes_correctly(api_client_with_auth, mock_db, mock_storage, mock_audit):
    """GET /podcasts/storage/usage が各 podcast の audio_url で blob size を取得し合計すること。"""
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    pod1 = Podcast(
        id="pod1",
        type="digest",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="podcasts/user1/digest1.mp3",
        japanese_intro_text="",
        duration_seconds=300,
        status="completed",
        created_at=now,
        user_id="user1",
    )

    mock_db.get_podcasts_for_user.return_value = [pod1]
    mock_storage.get_blob_size.return_value = 512000

    response = api_client_with_auth.get("/podcasts/storage/usage", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    data = response.json()
    assert data["total_bytes"] == 512000
    # get_blob_size が pod1.audio_url で呼ばれたこと
    mock_storage.get_blob_size.assert_called_with("podcasts/user1/digest1.mp3")


def test_get_storage_usage_handles_missing_blobs(api_client_with_auth, mock_db, mock_storage, mock_audit):
    """blob が存在しない場合（size=0）も加算する。"""
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    pod1 = Podcast(
        id="pod1",
        type="digest",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="missing/blob.mp3",
        japanese_intro_text="",
        duration_seconds=300,
        status="completed",
        created_at=now,
        user_id="user1",
    )

    mock_db.get_podcasts_for_user.return_value = [pod1]
    mock_storage.get_blob_size.return_value = 0  # blob not found

    response = api_client_with_auth.get("/podcasts/storage/usage", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    data = response.json()
    assert data["total_bytes"] == 0


def test_get_storage_usage_returns_empty_when_no_podcasts(api_client_with_auth, mock_db, mock_storage, mock_audit):
    """Podcast が 0 件の場合、total_bytes=0 / items=[] を返すこと。"""
    mock_db.get_podcasts_for_user.return_value = []

    response = api_client_with_auth.get("/podcasts/storage/usage", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    data = response.json()
    assert data["total_bytes"] == 0
    assert data["podcast_count"] == 0
    assert data["items"] == []
    # 誰も get_blob_size を呼ばない
    mock_storage.get_blob_size.assert_not_called()


def test_get_storage_usage_includes_items_with_correct_fields(api_client_with_auth, mock_db, mock_storage, mock_audit):
    """items が各 podcast の id / type / size_bytes / created_at を含むこと。"""
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    pod1 = Podcast(
        id="pod1",
        type="digest",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="podcasts/user1/digest1.mp3",
        japanese_intro_text="",
        duration_seconds=300,
        status="completed",
        created_at=now,
        user_id="user1",
    )

    mock_db.get_podcasts_for_user.return_value = [pod1]
    mock_storage.get_blob_size.return_value = 256000

    response = api_client_with_auth.get("/podcasts/storage/usage", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["id"] == "pod1"
    assert item["type"] == "digest"
    assert item["size_bytes"] == 256000
    assert item["created_at"] == now.isoformat()


# ---------- T6: POST /podcasts/storage/cleanup ----------


def test_cleanup_deletes_podcasts_and_blobs(api_client_with_auth, mock_db, mock_storage, mock_audit):
    """POST /podcasts/storage/cleanup が digest per-user podcast を削除し、blob を削除すること。"""
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    pod1 = Podcast(
        id="pod1",
        type="digest",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="podcasts/user1/digest1.mp3",
        japanese_intro_text="",
        duration_seconds=300,
        status="completed",
        created_at=now - timedelta(days=100),
        user_id="user1",
    )

    mock_db.get_podcasts_for_user.return_value = [pod1]
    mock_storage.get_blob_size.return_value = 512000

    response = api_client_with_auth.post(
        "/podcasts/storage/cleanup",
        json={"older_than_days": 30},
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["deleted_podcast_count"] == 1
    assert data["deleted_blob_count"] == 1
    assert data["freed_bytes"] == 512000
    # 監査ログが記録されたこと
    mock_audit.record.assert_called_once()
    audit_call = mock_audit.record.call_args
    assert audit_call[1]["action"] == "storage_cleanup"
    assert "deleted_podcast_count" in audit_call[1]["details"]


def test_cleanup_never_deletes_single_shared_cache(api_client_with_auth, mock_db, mock_storage, mock_audit):
    """single（shared cache）の blob は delete_blob が呼ばれないこと（最重要）。"""
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    pod1 = Podcast(
        id="pod1",
        type="single",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="podcasts/cache/art1__toeic_900__ja-en.mp3",
        japanese_intro_text="",
        duration_seconds=300,
        status="completed",
        created_at=now - timedelta(days=100),
        user_id="user1",
    )

    mock_db.get_podcasts_for_user.return_value = [pod1]
    mock_storage.get_blob_size.return_value = 256000

    response = api_client_with_auth.post(
        "/podcasts/storage/cleanup",
        json={"older_than_days": None},
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 200
    data = response.json()
    # Podcast doc は削除される（type 問わず）
    assert data["deleted_podcast_count"] == 1
    # しかし blob は削除されない（single は保護）
    assert data["deleted_blob_count"] == 0
    assert data["freed_bytes"] == 0
    # delete_blob が呼ばれていないこと（shared prefix で保護）
    mock_storage.delete_blob.assert_not_called()


def test_cleanup_filters_by_older_than_days(api_client_with_auth, mock_db, mock_storage, mock_audit):
    """older_than_days フィルタが正しく機能すること。"""
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    pod_old = Podcast(
        id="pod_old",
        type="digest",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="podcasts/user1/digest_old.mp3",
        japanese_intro_text="",
        duration_seconds=300,
        status="completed",
        created_at=now - timedelta(days=31),  # > 30 days → deleted
        user_id="user1",
    )
    pod_new = Podcast(
        id="pod_new",
        type="digest",
        article_ids=["art2"],
        difficulty="toeic_900",
        audio_url="podcasts/user1/digest_new.mp3",
        japanese_intro_text="",
        duration_seconds=300,
        status="completed",
        created_at=now - timedelta(days=10),  # < 30 days → not deleted
        user_id="user1",
    )

    mock_db.get_podcasts_for_user.return_value = [pod_old, pod_new]
    mock_storage.get_blob_size.side_effect = [100000, 200000]
    mock_storage.delete_blob.return_value = True  # 削除成功（解放済みとして計上）

    response = api_client_with_auth.post(
        "/podcasts/storage/cleanup",
        json={"older_than_days": 30},
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 200
    data = response.json()
    # old のみ削除される
    assert data["deleted_podcast_count"] == 1
    assert data["deleted_blob_count"] == 1
    assert data["freed_bytes"] == 100000


def test_cleanup_full_request_all_podcasts(api_client_with_auth, mock_db, mock_storage, mock_audit):
    """older_than_days=None で全件削除。"""
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    pod1 = Podcast(
        id="pod1",
        type="digest",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="podcasts/user1/digest1.mp3",
        japanese_intro_text="",
        duration_seconds=300,
        status="completed",
        created_at=now - timedelta(days=1),
        user_id="user1",
    )

    mock_db.get_podcasts_for_user.return_value = [pod1]
    mock_storage.get_blob_size.return_value = 512000

    response = api_client_with_auth.post(
        "/podcasts/storage/cleanup",
        json={"older_than_days": None},
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["deleted_podcast_count"] == 1


def test_cleanup_validates_older_than_days_negative(api_client_with_auth):
    """older_than_days < 0 は 422 を返すこと。"""
    response = api_client_with_auth.post(
        "/podcasts/storage/cleanup",
        json={"older_than_days": -1},
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 422


def test_cleanup_is_idempotent(api_client_with_auth, mock_db, mock_storage, mock_audit):
    """cleanup を複数回呼ぶと冪等（2回目は 0 削除）。"""
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    pod1 = Podcast(
        id="pod1",
        type="digest",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="podcasts/user1/digest1.mp3",
        japanese_intro_text="",
        duration_seconds=300,
        status="completed",
        created_at=now - timedelta(days=100),
        user_id="user1",
    )

    # 1回目: 削除対象あり
    mock_db.get_podcasts_for_user.return_value = [pod1]
    mock_storage.get_blob_size.return_value = 512000

    response1 = api_client_with_auth.post(
        "/podcasts/storage/cleanup",
        json={"older_than_days": 30},
        headers={"X-API-Key": "test-key"},
    )
    assert response1.status_code == 200
    assert response1.json()["deleted_podcast_count"] == 1

    # 2回目: 削除対象なし
    mock_db.get_podcasts_for_user.return_value = []

    response2 = api_client_with_auth.post(
        "/podcasts/storage/cleanup",
        json={"older_than_days": 30},
        headers={"X-API-Key": "test-key"},
    )
    assert response2.status_code == 200
    assert response2.json()["deleted_podcast_count"] == 0


def test_cleanup_audit_details_exclude_ids(api_client_with_auth, mock_db, mock_storage, mock_audit):
    """監査ログの details に podcast id / blob path / article_id が入らないこと（プライバシー）。"""
    now = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    pod1 = Podcast(
        id="pod1",
        type="digest",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="podcasts/user1/digest1.mp3",
        japanese_intro_text="",
        duration_seconds=300,
        status="completed",
        created_at=now - timedelta(days=100),
        user_id="user1",
    )

    mock_db.get_podcasts_for_user.return_value = [pod1]
    mock_storage.get_blob_size.return_value = 512000

    api_client_with_auth.post(
        "/podcasts/storage/cleanup",
        json={"older_than_days": 30},
        headers={"X-API-Key": "test-key"},
    )

    mock_audit.record.assert_called_once()
    audit_call = mock_audit.record.call_args
    details = audit_call[1]["details"]
    # details に pod_id / blob_path / article_id が無いこと
    assert "pod1" not in str(details)
    assert "podcasts/user1/digest1.mp3" not in str(details)
    assert "art1" not in str(details)
