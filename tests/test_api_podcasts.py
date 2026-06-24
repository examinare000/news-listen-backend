"""GET /podcasts, GET /podcasts/{id} のテスト。"""
from datetime import datetime, timezone
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
