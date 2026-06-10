"""GET/POST/DELETE /settings/sources のテスト。"""
from shared.models import RssSource, UserPrefs


def test_get_sources_returns_default_sources(api_client, mock_db):
    mock_db.get_user_prefs.return_value = UserPrefs(
        user_id="user1", default_difficulty="toeic_900"
    )

    response = api_client.get("/settings/sources", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    sources = response.json()["sources"]
    assert len(sources) == 0  # UserPrefs.rss_sources のデフォルトは空リスト


def test_add_source_saves_new_source(api_client, mock_db):
    mock_db.get_user_prefs.return_value = UserPrefs(
        user_id="user1", default_difficulty="toeic_900"
    )

    response = api_client.post(
        "/settings/sources",
        json={"name": "TechCrunch", "url": "https://techcrunch.com/feed/"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    mock_db.save_user_prefs.assert_called_once()


def test_add_duplicate_source_returns_409(api_client, mock_db):
    prefs = UserPrefs(
        user_id="user1",
        default_difficulty="toeic_900",
        rss_sources=[RssSource(name="Existing", url="https://existing.com/feed")],
    )
    mock_db.get_user_prefs.return_value = prefs

    response = api_client.post(
        "/settings/sources",
        json={"name": "Existing", "url": "https://existing.com/feed"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 409


def test_delete_source_removes_existing_source(api_client, mock_db):
    """DELETE /settings/sources が既存のソースを削除する"""
    prefs = UserPrefs(
        user_id="user1",
        default_difficulty="toeic_900",
        rss_sources=[
            RssSource(name="TechCrunch", url="https://techcrunch.com/feed"),
            RssSource(name="HackerNews", url="https://news.ycombinator.com/rss"),
        ],
    )
    mock_db.get_user_prefs.return_value = prefs

    response = api_client.request(
        "DELETE",
        "/settings/sources",
        params={"url": "https://techcrunch.com/feed"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    # save_user_prefs が呼ばれ、残ったソースは1件
    mock_db.save_user_prefs.assert_called_once()
    saved_prefs = mock_db.save_user_prefs.call_args[0][0]
    assert len(saved_prefs.rss_sources) == 1
    assert saved_prefs.rss_sources[0].name == "HackerNews"


def test_delete_source_returns_404_when_not_found(api_client, mock_db):
    """DELETE /settings/sources で存在しない URL を指定すると 404"""
    mock_db.get_user_prefs.return_value = UserPrefs(
        user_id="user1", default_difficulty="toeic_900"
    )

    response = api_client.request(
        "DELETE",
        "/settings/sources",
        params={"url": "https://nonexistent.com/feed"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 404
