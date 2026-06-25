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


def test_get_sources_returns_default_sources(api_client_with_auth, mock_db):
    mock_db.get_user_prefs.return_value = UserPrefs(
        user_id="user1", default_difficulty="toeic_900"
    )

    response = api_client_with_auth.get("/settings/sources", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    sources = response.json()["sources"]
    assert len(sources) == 0  # UserPrefs.rss_sources のデフォルトは空リスト


def test_add_source_saves_new_source(api_client_with_auth, mock_db):
    mock_db.get_user_prefs.return_value = UserPrefs(
        user_id="user1", default_difficulty="toeic_900"
    )

    response = api_client_with_auth.post(
        "/settings/sources",
        json={"name": "TechCrunch", "url": "https://techcrunch.com/feed/"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    mock_db.save_user_prefs.assert_called_once()


def test_add_duplicate_source_returns_409(api_client_with_auth, mock_db):
    prefs = UserPrefs(
        user_id="user1",
        default_difficulty="toeic_900",
        rss_sources=[RssSource(name="Existing", url="https://existing.com/feed")],
    )
    mock_db.get_user_prefs.return_value = prefs

    response = api_client_with_auth.post(
        "/settings/sources",
        json={"name": "Existing", "url": "https://existing.com/feed"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 409


def test_delete_source_removes_existing_source(api_client_with_auth, mock_db):
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

    response = api_client_with_auth.request(
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


def test_delete_source_returns_404_when_not_found(api_client_with_auth, mock_db):
    """DELETE /settings/sources で存在しない URL を指定すると 404"""
    mock_db.get_user_prefs.return_value = UserPrefs(
        user_id="user1", default_difficulty="toeic_900"
    )

    response = api_client_with_auth.request(
        "DELETE",
        "/settings/sources",
        params={"url": "https://nonexistent.com/feed"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 404


# ---------- GET /settings/featured-sources ----------


def test_get_featured_sources_returns_empty_list(api_client_with_auth, mock_db):
    mock_db.get_featured_sites.return_value = []

    response = api_client_with_auth.get(
        "/settings/featured-sources", headers={"X-API-Key": "test-key"}
    )
    assert response.status_code == 200
    assert response.json()["sites"] == []


def test_get_featured_sources_returns_sites_in_order(api_client_with_auth, mock_db):
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

    response = api_client_with_auth.get(
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


def test_get_onboarding_defaults_to_false(api_client_with_auth, mock_db):
    mock_db.get_user_prefs.return_value = UserPrefs(
        user_id="user1", default_difficulty="toeic_900"
    )

    response = api_client_with_auth.get("/settings/onboarding", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    assert response.json()["onboarding_completed"] is False


def test_complete_onboarding_sets_flag_true_and_persists(api_client_with_auth, mock_db):
    mock_db.get_user_prefs.return_value = UserPrefs(
        user_id="user1", default_difficulty="toeic_900"
    )

    response = api_client_with_auth.post(
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


# ---------- GET/PUT /settings/preferences ----------


def test_get_preferences_returns_200(api_client_with_auth, mock_db):
    """GET /settings/preferences が UserPrefs の4フィールドを返す（契約検証）。"""
    mock_db.get_user_prefs.return_value = UserPrefs(
        user_id="user1",
        default_difficulty="toeic_900",
        default_playback_speed=1.5,
        digest_enabled=True,
        digest_article_count=7,
    )

    response = api_client_with_auth.get("/settings/preferences", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    data = response.json()
    # D. 契約検証
    assert data["default_difficulty"] == "toeic_900"
    assert data["default_playback_speed"] == 1.5
    assert data["digest_enabled"] is True
    assert data["digest_article_count"] == 7


def test_put_preferences_updates_difficulty(api_client_with_auth, mock_db):
    """PUT /settings/preferences で difficulty を更新し永続化する。"""
    original = UserPrefs(
        user_id="user1",
        default_difficulty="toeic_900",
        default_playback_speed=1.0,
        digest_enabled=True,
        digest_article_count=5,
    )
    mock_db.get_user_prefs.return_value = original

    response = api_client_with_auth.put(
        "/settings/preferences",
        json={"default_difficulty": "ielts_7"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["default_difficulty"] == "ielts_7"

    # save_user_prefs が default_difficulty="ielts_7" を含む prefs で呼ばれること
    mock_db.save_user_prefs.assert_called_once()
    saved = mock_db.save_user_prefs.call_args[0][0]
    assert saved.default_difficulty == "ielts_7"


def test_put_preferences_partial_update_preserves_others(api_client_with_auth, mock_db):
    """PUT /settings/preferences で default_difficulty のみ送信、他フィールド保持。"""
    original = UserPrefs(
        user_id="user1",
        default_difficulty="toeic_900",
        default_playback_speed=1.5,
        digest_enabled=False,
        digest_article_count=10,
    )
    mock_db.get_user_prefs.return_value = original

    response = api_client_with_auth.put(
        "/settings/preferences",
        json={"default_difficulty": "eiken_p1"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 200
    data = response.json()
    # 送信したフィールド
    assert data["default_difficulty"] == "eiken_p1"
    # 未送信フィールドは元値を保持
    assert data["default_playback_speed"] == 1.5
    assert data["digest_enabled"] is False
    assert data["digest_article_count"] == 10

    # save_user_prefs で他フィールドが保持されていることを検証
    saved = mock_db.save_user_prefs.call_args[0][0]
    assert saved.default_playback_speed == 1.5
    assert saved.digest_enabled is False
    assert saved.digest_article_count == 10


def test_put_preferences_invalid_difficulty_returns_422(api_client_with_auth, mock_db):
    """PUT /settings/preferences で不正な difficulty → 422。"""
    mock_db.get_user_prefs.return_value = UserPrefs(
        user_id="user1", default_difficulty="toeic_900"
    )

    response = api_client_with_auth.put(
        "/settings/preferences",
        json={"default_difficulty": "invalid_difficulty"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 422


def test_put_preferences_invalid_digest_count_returns_422(api_client_with_auth, mock_db):
    """PUT /settings/preferences で digest_article_count < 1 → 422。"""
    mock_db.get_user_prefs.return_value = UserPrefs(
        user_id="user1", default_difficulty="toeic_900"
    )

    response = api_client_with_auth.put(
        "/settings/preferences",
        json={"digest_article_count": 0},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 422


def test_add_source_records_audit_log(api_client_with_auth, mock_db, mock_audit):
    """add_source が成功時に audit.record を正しい action・details で呼ぶ。"""
    mock_db.get_user_prefs.return_value = UserPrefs(
        user_id="user1", default_difficulty="toeic_900"
    )

    response = api_client_with_auth.post(
        "/settings/sources",
        json={"name": "TechCrunch", "url": "https://techcrunch.com/feed/"},
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 200
    # audit.record が "rss_source_add" action で呼ばれたことを確認
    mock_audit.record.assert_called_once()
    call_kwargs = mock_audit.record.call_args.kwargs
    assert call_kwargs["action"] == "rss_source_add"
    assert call_kwargs["details"]["url"] == "https://techcrunch.com/feed/"
    assert call_kwargs["details"]["name"] == "TechCrunch"


def test_add_duplicate_source_does_not_record(api_client_with_auth, mock_db, mock_audit):
    """重複 RSS ソース追加時（409）は audit.record を呼ばない。"""
    prefs = UserPrefs(
        user_id="user1",
        default_difficulty="toeic_900",
        rss_sources=[RssSource(name="TechCrunch", url="https://techcrunch.com/feed/")],
    )
    mock_db.get_user_prefs.return_value = prefs

    response = api_client_with_auth.post(
        "/settings/sources",
        json={"name": "TechCrunch", "url": "https://techcrunch.com/feed/"},
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 409
    mock_audit.record.assert_not_called()


def test_remove_source_records_audit_log(api_client_with_auth, mock_db, mock_audit):
    """remove_source が成功時に audit.record を正しい action・details で呼ぶ。"""
    prefs = UserPrefs(
        user_id="user1",
        default_difficulty="toeic_900",
        rss_sources=[RssSource(name="TechCrunch", url="https://techcrunch.com/feed/")],
    )
    mock_db.get_user_prefs.return_value = prefs

    response = api_client_with_auth.delete(
        "/settings/sources?url=https://techcrunch.com/feed/",
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 200
    # audit.record が "rss_source_remove" action で呼ばれたことを確認
    mock_audit.record.assert_called_once()
    call_kwargs = mock_audit.record.call_args.kwargs
    assert call_kwargs["action"] == "rss_source_remove"
    assert call_kwargs["details"]["url"] == "https://techcrunch.com/feed/"


def test_remove_nonexistent_source_does_not_record(api_client_with_auth, mock_db, mock_audit):
    """存在しない RSS ソース削除時（404）は audit.record を呼ばない。"""
    mock_db.get_user_prefs.return_value = UserPrefs(
        user_id="user1", default_difficulty="toeic_900"
    )

    response = api_client_with_auth.delete(
        "/settings/sources?url=https://nonexistent.com/feed/",
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 404
    mock_audit.record.assert_not_called()


def test_update_preferences_records_audit_log(api_client_with_auth, mock_db, mock_audit):
    """update_preferences が成功時に audit.record を正しい action・details で呼ぶ。"""
    mock_db.get_user_prefs.return_value = UserPrefs(
        user_id="user1", default_difficulty="toeic_900"
    )

    response = api_client_with_auth.put(
        "/settings/preferences",
        json={"default_difficulty": "eiken_p1", "default_playback_speed": 1.25},
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 200
    # audit.record が "preferences_update" action で呼ばれたことを確認
    mock_audit.record.assert_called_once()
    call_kwargs = mock_audit.record.call_args.kwargs
    assert call_kwargs["action"] == "preferences_update"
    # 変更フィールド名のリストであること（値は入らない）
    assert "default_difficulty" in call_kwargs["details"]["fields"]
    assert "default_playback_speed" in call_kwargs["details"]["fields"]
    # 値がリストに含まれていないことを確認（セキュリティ: 機微情報非記録）
    assert "eiken_p1" not in call_kwargs["details"]["fields"]
    assert 1.25 not in call_kwargs["details"]["fields"]


def test_complete_onboarding_records_audit_log(api_client_with_auth, mock_db, mock_audit):
    """complete_onboarding が成功時に audit.record を正しい action で呼ぶ。"""
    mock_db.get_user_prefs.return_value = UserPrefs(
        user_id="user1", default_difficulty="toeic_900", onboarding_completed=False
    )

    response = api_client_with_auth.post(
        "/settings/onboarding/complete",
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 200
    # audit.record が "onboarding_complete" action で呼ばれたことを確認
    mock_audit.record.assert_called_once()
    call_kwargs = mock_audit.record.call_args.kwargs
    assert call_kwargs["action"] == "onboarding_complete"
    # details は不要（シンプルなフラグ更新なので key がなくても OK）
    assert "details" not in call_kwargs or call_kwargs.get("details") is None
