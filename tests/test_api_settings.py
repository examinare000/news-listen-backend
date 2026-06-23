"""GET/POST/DELETE /settings/sources と featured-sources / onboarding のテスト。"""
import socket

import pytest

from shared.models import FeaturedSite, RssSource, UserPrefs


@pytest.fixture(autouse=True)
def _stub_dns(monkeypatch):
    """SSRF バリデータの socket.getaddrinfo を公開 IP に固定する。

    URL 登録時の field_validator が実 DNS を引くとテストがネットワーク依存・低速・
    フレーキーになるため、外部依存の DNS をスタブ化する（検証ロジック自体は実行される）。
    """
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )


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


# ---------- GET /settings/featured-sources ----------


def test_get_featured_sources_returns_empty_list(api_client, mock_db):
    mock_db.get_featured_sites.return_value = []

    response = api_client.get(
        "/settings/featured-sources", headers={"X-API-Key": "test-key"}
    )
    assert response.status_code == 200
    assert response.json()["sites"] == []


def test_get_featured_sources_returns_sites_in_order(api_client, mock_db):
    # get_featured_sites は order 昇順で返す責務（ここではその並びをそのまま検証）
    mock_db.get_featured_sites.return_value = [
        FeaturedSite(
            id="the-verge",
            name="The Verge",
            url="https://www.theverge.com/rss/index.xml",
            thumbnail_url="https://www.theverge.com/favicon.ico",
            description="テクノロジー全般",
            order=0,
        ),
        FeaturedSite(
            id="techcrunch",
            name="TechCrunch",
            url="https://techcrunch.com/feed/",
            order=1,
        ),
    ]

    response = api_client.get(
        "/settings/featured-sources", headers={"X-API-Key": "test-key"}
    )
    assert response.status_code == 200
    sites = response.json()["sites"]
    assert [s["id"] for s in sites] == ["the-verge", "techcrunch"]
    assert sites[0]["thumbnail_url"] == "https://www.theverge.com/favicon.ico"
    # order はレスポンススキーマに含めない（表示順は配列順で表現する）
    assert "order" not in sites[0]
    assert sites[1]["thumbnail_url"] is None


# ---------- GET/POST /settings/onboarding ----------


def test_get_onboarding_defaults_to_false(api_client, mock_db):
    mock_db.get_user_prefs.return_value = UserPrefs(
        user_id="user1", default_difficulty="toeic_900"
    )

    response = api_client.get("/settings/onboarding", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    assert response.json()["onboarding_completed"] is False


def test_complete_onboarding_sets_flag_true_and_persists(api_client, mock_db):
    mock_db.get_user_prefs.return_value = UserPrefs(
        user_id="user1", default_difficulty="toeic_900"
    )

    response = api_client.post(
        "/settings/onboarding/complete", headers={"X-API-Key": "test-key"}
    )
    assert response.status_code == 200
    assert response.json()["onboarding_completed"] is True

    # 永続化: save_user_prefs に onboarding_completed=True が渡る
    mock_db.save_user_prefs.assert_called_once()
    saved = mock_db.save_user_prefs.call_args[0][0]
    assert saved.onboarding_completed is True
    # 他の必須フィールドが保持されている（全置換更新でも欠落しない）
    assert saved.default_difficulty == "toeic_900"
