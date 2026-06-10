"""GET /podcasts, GET /podcasts/{id} のテスト。"""
from datetime import datetime, timezone
from shared.models import Podcast


def _make_podcast(podcast_id="pod1"):
    return Podcast(
        id=podcast_id,
        type="single",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="podcasts/pod1/toeic_900.mp3",
        japanese_intro_text="日本語イントロ",
        duration_seconds=300,
        status="completed",
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
