"""管理用 /admin/featured-sites CRUD のテスト。"""
import socket
from datetime import datetime, timedelta, timezone

import pytest

from shared.models import FeaturedSite, Session


NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)
API_HEADERS = {"X-API-Key": "test-key"}
AUTH = {**API_HEADERS, "Authorization": "Bearer raw"}


def _session(role: str) -> Session:
    """admin または user ロールのセッションを生成する。"""
    return Session(
        session_id="hashed",
        user_id="uid-admin",
        username="admin",
        role=role,
        created_at=NOW,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


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


def test_list_featured_sites(api_client, mock_db):
    mock_db.get_session.return_value = _session("admin")
    mock_db.get_featured_sites.return_value = [
        FeaturedSite(id="the-verge", name="The Verge", url="https://www.theverge.com/rss/index.xml", order=0),
    ]

    response = api_client.get("/admin/featured-sites", headers=AUTH)
    assert response.status_code == 200
    assert [s["id"] for s in response.json()["sites"]] == ["the-verge"]


def test_create_featured_site(api_client, mock_db):
    # 新規作成: 既存チェックは None（未登録）
    mock_db.get_session.return_value = _session("admin")
    mock_db.get_featured_site.return_value = None

    response = api_client.post(
        "/admin/featured-sites",
        json={
            "name": "Wired (Technology)",
            "url": "https://www.wired.com/feed/category/business/latest/rss",
            "thumbnail_url": "https://www.wired.com/favicon.ico",
            "description": "テクノロジー",
            "order": 3,
        },
        headers=AUTH,
    )
    assert response.status_code == 201
    body = response.json()
    # name から slug 化された doc id
    assert body["id"] == "wired-technology"
    mock_db.save_featured_site.assert_called_once()
    saved = mock_db.save_featured_site.call_args[0][0]
    assert saved.id == "wired-technology"
    assert saved.order == 3
    # HttpUrl は str に正規化して保存される
    assert isinstance(saved.url, str)


def test_create_duplicate_featured_site_returns_409(api_client, mock_db):
    mock_db.get_session.return_value = _session("admin")
    mock_db.get_featured_site.return_value = FeaturedSite(
        id="techcrunch", name="TechCrunch", url="https://techcrunch.com/feed/"
    )

    response = api_client.post(
        "/admin/featured-sites",
        json={"name": "TechCrunch", "url": "https://techcrunch.com/feed/"},
        headers=AUTH,
    )
    assert response.status_code == 409


def test_update_featured_site(api_client, mock_db):
    mock_db.get_session.return_value = _session("admin")
    mock_db.get_featured_site.return_value = FeaturedSite(
        id="techcrunch", name="TechCrunch", url="https://techcrunch.com/feed/"
    )

    response = api_client.put(
        "/admin/featured-sites/techcrunch",
        json={"name": "TechCrunch JP", "url": "https://jp.techcrunch.com/feed/", "order": 5},
        headers=AUTH,
    )
    assert response.status_code == 200
    saved = mock_db.save_featured_site.call_args[0][0]
    assert saved.id == "techcrunch"  # path の id を維持
    assert saved.name == "TechCrunch JP"
    assert saved.order == 5


def test_update_missing_featured_site_returns_404(api_client, mock_db):
    mock_db.get_session.return_value = _session("admin")
    mock_db.get_featured_site.return_value = None

    response = api_client.put(
        "/admin/featured-sites/ghost",
        json={"name": "Ghost", "url": "https://example.com/feed"},
        headers=AUTH,
    )
    assert response.status_code == 404


def test_delete_featured_site(api_client, mock_db):
    mock_db.get_session.return_value = _session("admin")
    mock_db.get_featured_site.return_value = FeaturedSite(
        id="techcrunch", name="TechCrunch", url="https://techcrunch.com/feed/"
    )

    response = api_client.request(
        "DELETE", "/admin/featured-sites/techcrunch", headers=AUTH
    )
    assert response.status_code == 200
    mock_db.delete_featured_site.assert_called_once_with("techcrunch")


def test_delete_missing_featured_site_returns_404(api_client, mock_db):
    mock_db.get_session.return_value = _session("admin")
    mock_db.get_featured_site.return_value = None

    response = api_client.request(
        "DELETE", "/admin/featured-sites/ghost", headers=AUTH
    )
    assert response.status_code == 404


def test_admin_requires_api_key(api_client, mock_db):
    # X-API-Key 無しは 401
    response = api_client.get("/admin/featured-sites")
    assert response.status_code == 401


class TestFeaturedSitesAuthorization:
    """featured-sites エンドポイントの admin ロール認可を検証する。"""

    def test_non_admin_list_returns_403(self, api_client, mock_db):
        mock_db.get_session.return_value = _session("user")
        resp = api_client.get("/admin/featured-sites", headers=AUTH)
        assert resp.status_code == 403

    def test_non_admin_create_returns_403(self, api_client, mock_db):
        mock_db.get_session.return_value = _session("user")
        resp = api_client.post(
            "/admin/featured-sites",
            json={"name": "Test", "url": "https://example.com/feed"},
            headers=AUTH,
        )
        assert resp.status_code == 403

    def test_non_admin_update_returns_403(self, api_client, mock_db):
        mock_db.get_session.return_value = _session("user")
        resp = api_client.put(
            "/admin/featured-sites/test",
            json={"name": "Test", "url": "https://example.com/feed"},
            headers=AUTH,
        )
        assert resp.status_code == 403

    def test_non_admin_delete_returns_403(self, api_client, mock_db):
        mock_db.get_session.return_value = _session("user")
        resp = api_client.delete("/admin/featured-sites/test", headers=AUTH)
        assert resp.status_code == 403

    def test_unauthenticated_list_returns_401(self, api_client, mock_db):
        resp = api_client.get("/admin/featured-sites", headers=API_HEADERS)
        assert resp.status_code == 401

    def test_unauthenticated_create_returns_401(self, api_client, mock_db):
        resp = api_client.post(
            "/admin/featured-sites",
            json={"name": "Test", "url": "https://example.com/feed"},
            headers=API_HEADERS,
        )
        assert resp.status_code == 401

    def test_unauthenticated_update_returns_401(self, api_client, mock_db):
        resp = api_client.put(
            "/admin/featured-sites/test",
            json={"name": "Test", "url": "https://example.com/feed"},
            headers=API_HEADERS,
        )
        assert resp.status_code == 401

    def test_unauthenticated_delete_returns_401(self, api_client, mock_db):
        resp = api_client.delete("/admin/featured-sites/test", headers=API_HEADERS)
        assert resp.status_code == 401
