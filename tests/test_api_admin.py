"""管理用 /admin/featured-sites CRUD のテスト。"""
import socket

import pytest

from shared.models import FeaturedSite


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
    mock_db.get_featured_sites.return_value = [
        FeaturedSite(id="the-verge", name="The Verge", url="https://www.theverge.com/rss/index.xml", order=0),
    ]

    response = api_client.get("/admin/featured-sites", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    assert [s["id"] for s in response.json()["sites"]] == ["the-verge"]


def test_create_featured_site(api_client, mock_db):
    # 新規作成: 既存チェックは None（未登録）
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
        headers={"X-API-Key": "test-key"},
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
    mock_db.get_featured_site.return_value = FeaturedSite(
        id="techcrunch", name="TechCrunch", url="https://techcrunch.com/feed/"
    )

    response = api_client.post(
        "/admin/featured-sites",
        json={"name": "TechCrunch", "url": "https://techcrunch.com/feed/"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 409


def test_update_featured_site(api_client, mock_db):
    mock_db.get_featured_site.return_value = FeaturedSite(
        id="techcrunch", name="TechCrunch", url="https://techcrunch.com/feed/"
    )

    response = api_client.put(
        "/admin/featured-sites/techcrunch",
        json={"name": "TechCrunch JP", "url": "https://jp.techcrunch.com/feed/", "order": 5},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    saved = mock_db.save_featured_site.call_args[0][0]
    assert saved.id == "techcrunch"  # path の id を維持
    assert saved.name == "TechCrunch JP"
    assert saved.order == 5


def test_update_missing_featured_site_returns_404(api_client, mock_db):
    mock_db.get_featured_site.return_value = None

    response = api_client.put(
        "/admin/featured-sites/ghost",
        json={"name": "Ghost", "url": "https://example.com/feed"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 404


def test_delete_featured_site(api_client, mock_db):
    mock_db.get_featured_site.return_value = FeaturedSite(
        id="techcrunch", name="TechCrunch", url="https://techcrunch.com/feed/"
    )

    response = api_client.request(
        "DELETE", "/admin/featured-sites/techcrunch", headers={"X-API-Key": "test-key"}
    )
    assert response.status_code == 200
    mock_db.delete_featured_site.assert_called_once_with("techcrunch")


def test_delete_missing_featured_site_returns_404(api_client, mock_db):
    mock_db.get_featured_site.return_value = None

    response = api_client.request(
        "DELETE", "/admin/featured-sites/ghost", headers={"X-API-Key": "test-key"}
    )
    assert response.status_code == 404


def test_admin_requires_api_key(api_client, mock_db):
    # X-API-Key 無しは 401
    response = api_client.get("/admin/featured-sites")
    assert response.status_code == 401
