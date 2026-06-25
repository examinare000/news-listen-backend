"""api.schemas の単体テスト。"""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from shared.models import Podcast
from api.schemas import PodcastResponse, RssSourceRequest, FeaturedSiteRequest


NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)


def _make_podcast(**overrides) -> Podcast:
    defaults = dict(
        id="pod1",
        type="single",
        article_ids=["art1"],
        difficulty="toeic_900",
        audio_url="podcasts/pod1/toeic_900.mp3",
        japanese_intro_text="日本語イントロ",
        duration_seconds=300,
        status="completed",
        created_at=NOW,
        user_id="user1",
    )
    defaults.update(overrides)
    return Podcast(**defaults)


def test_podcast_response_from_podcast_classmethod_exists():
    """PodcastResponse.from_podcast() クラスメソッドが存在すること。"""
    assert hasattr(PodcastResponse, "from_podcast"), (
        "PodcastResponse.from_podcast() classmethod が未実装。"
        "list_podcasts / get_podcast の重複コードを解消するために必要。"
    )


def test_podcast_response_from_podcast_maps_all_fields():
    """from_podcast() が Podcast モデルの全フィールドを正しくマッピングすること。"""
    podcast = _make_podcast()
    response = PodcastResponse.from_podcast(podcast)

    assert response.id == "pod1"
    assert response.type == "single"
    assert response.article_ids == ["art1"]
    assert response.difficulty == "toeic_900"
    assert response.audio_url == "podcasts/pod1/toeic_900.mp3"
    assert response.japanese_intro_text == "日本語イントロ"
    assert response.duration_seconds == 300
    assert response.created_at == NOW.isoformat()


def test_podcast_response_from_podcast_preserves_created_at_as_iso():
    """created_at は ISO 8601 文字列に変換されること。"""
    podcast = _make_podcast()
    response = PodcastResponse.from_podcast(podcast)
    assert response.created_at == "2026-05-31T12:00:00+00:00"


def test_podcast_response_from_podcast_uses_audio_url_override():
    """audio_url 引数が指定された場合は Podcast.audio_url の代わりに使用されること。

    API ルーターは GCS blob path を StorageClient.generate_audio_url() で
    署名付き URL に変換してから from_podcast() に渡す設計。
    from_podcast() は渡された audio_url を優先する必要がある。
    """
    podcast = _make_podcast()
    signed_url = "https://storage.googleapis.com/bucket/signed-url-xyz"
    response = PodcastResponse.from_podcast(podcast, audio_url=signed_url)
    assert response.audio_url == signed_url


# === SSRF 対策：URL 検証 ===


def test_rss_source_request_accepts_safe_url():
    """RssSourceRequest が安全 URL を受理すること（validate_url パッチ）。"""
    with patch("shared.url_guard.validate_url") as mock_validate:
        mock_validate.return_value = ["93.184.216.34"]
        req = RssSourceRequest(name="example", url="https://example.com/rss")
        assert req.name == "example"
        # HttpUrl は末尾スラッシュを保持 or 追加する可能性がある
        assert "example.com" in str(req.url)


def test_rss_source_request_rejects_unsafe_url():
    """RssSourceRequest が危険 URL を拒否して ValidationError を raise すること。"""
    from shared.url_guard import UnsafeUrlError

    with patch("shared.url_guard.validate_url") as mock_validate:
        mock_validate.side_effect = UnsafeUrlError("private_ip")
        with pytest.raises(ValidationError) as exc_info:
            RssSourceRequest(name="example", url="http://169.254.169.254/rss")
        # ValidationError に 'unsafe url' 関連のメッセージが含まれることを確認
        assert "unsafe url" in str(exc_info.value).lower() or "private_ip" in str(exc_info.value).lower()


def test_featured_site_request_accepts_safe_url():
    """FeaturedSiteRequest が安全 URL を受理すること。"""
    with patch("shared.url_guard.validate_url") as mock_validate:
        mock_validate.return_value = ["93.184.216.34"]
        req = FeaturedSiteRequest(
            name="site",
            url="https://example.com",
            thumbnail_url="https://example.com/thumb.png",
            description="Test site",
        )
        assert req.name == "site"
        # HttpUrl は末尾スラッシュを保持 or 追加する可能性がある
        assert "example.com" in str(req.url)


def test_featured_site_request_rejects_unsafe_url():
    """FeaturedSiteRequest が危険 URL を拒否して ValidationError を raise すること。"""
    from shared.url_guard import UnsafeUrlError

    with patch("shared.url_guard.validate_url") as mock_validate:
        mock_validate.side_effect = UnsafeUrlError("private_ip")
        with pytest.raises(ValidationError):
            FeaturedSiteRequest(
                name="site",
                url="http://10.0.0.1/site",
                description="Internal site",
            )


def test_featured_site_request_skips_thumbnail_url_validation_when_none():
    """FeaturedSiteRequest の thumbnail_url が None の場合，validate_url を呼ばないこと。"""
    with patch("shared.url_guard.validate_url") as mock_validate:
        mock_validate.return_value = ["93.184.216.34"]
        FeaturedSiteRequest(
            name="site",
            url="https://example.com",
            thumbnail_url=None,
            description="Test site",
        )
        # url は検証されるが，thumbnail_url は None なので検証されない
        # validate_url は 1 回だけ呼ばれる（url field のみ）
        assert mock_validate.call_count == 1


# === パスワード強度検証 ===


class TestUserCreateRequestPasswordValidation:
    """UserCreateRequest のパスワード強度検証。"""

    def test_weak_password_raises_validation_error(self):
        """弱いパスワードは ValidationError を raise。"""
        from api.schemas import UserCreateRequest

        with pytest.raises(ValidationError) as exc_info:
            UserCreateRequest(
                username="alice",
                password="weak",  # 短すぎる
                role="user",
            )
        assert "at least 12 characters" in str(exc_info.value).lower()

    def test_password_equals_username_raises_validation_error(self):
        """password == username は ValidationError を raise。"""
        from api.schemas import UserCreateRequest

        with pytest.raises(ValidationError) as exc_info:
            UserCreateRequest(
                username="MyPassword123!",
                password="MyPassword123!",  # username と同じ
                role="user",
            )
        assert "must not contain the username" in str(exc_info.value).lower()

    def test_password_contains_username_raises_validation_error(self):
        """password に username を部分文字列として含む場合は ValidationError。"""
        from api.schemas import UserCreateRequest

        with pytest.raises(ValidationError) as exc_info:
            UserCreateRequest(
                username="alice",  # 5文字
                password="alice2024Secret!",  # "alice" を含む
                role="user",
            )
        assert "must not contain the username" in str(exc_info.value).lower()

    def test_strong_password_accepted(self):
        """強いパスワードは受理。"""
        from api.schemas import UserCreateRequest

        req = UserCreateRequest(
            username="bob",
            password="Str0ng-Pass!23",
            role="user",
        )
        assert req.username == "bob"
        assert req.password == "Str0ng-Pass!23"


class TestPasswordChangeRequestValidation:
    """PasswordChangeRequest のパスワード強度検証。"""

    def test_weak_new_password_raises_validation_error(self):
        """弱い new_password は ValidationError。"""
        from api.schemas import PasswordChangeRequest

        with pytest.raises(ValidationError) as exc_info:
            PasswordChangeRequest(
                current_password="OldPass123!",
                new_password="weak",
            )
        assert "at least 12 characters" in str(exc_info.value).lower()

    def test_strong_new_password_accepted(self):
        """強い new_password は受理。"""
        from api.schemas import PasswordChangeRequest

        req = PasswordChangeRequest(
            current_password="OldPass123!",
            new_password="NewPass123!456",
        )
        assert req.new_password == "NewPass123!456"


class TestUserUpdateRequestValidation:
    """UserUpdateRequest のパスワード強度検証。"""

    def test_weak_new_password_raises_validation_error(self):
        """弱い new_password は ValidationError。"""
        from api.schemas import UserUpdateRequest

        with pytest.raises(ValidationError) as exc_info:
            UserUpdateRequest(
                new_password="weak",
            )
        assert "at least 12 characters" in str(exc_info.value).lower()

    def test_new_password_none_skips_validation(self):
        """new_password=None（または未指定）の場合は検証をスキップ。"""
        from api.schemas import UserUpdateRequest

        # None を明示的に指定
        req = UserUpdateRequest(new_password=None)
        assert req.new_password is None

        # 未指定（デフォルト）
        req = UserUpdateRequest()
        assert req.new_password is None

    def test_strong_new_password_accepted(self):
        """強い new_password は受理。"""
        from api.schemas import UserUpdateRequest

        req = UserUpdateRequest(
            role="admin",
            new_password="Str0ng-Pass!23",
        )
        assert req.new_password == "Str0ng-Pass!23"
        assert req.role == "admin"


# ---------- T4: StorageUsageItem / StorageUsageResponse / StorageCleanupRequest / StorageCleanupResponse ----------


def test_storage_cleanup_request_validates_older_than_days():
    """StorageCleanupRequest が older_than_days >= 0 を検証すること。"""
    from api.schemas import StorageCleanupRequest

    # 正常値
    req = StorageCleanupRequest(older_than_days=10)
    assert req.older_than_days == 10

    # None（全件）
    req = StorageCleanupRequest(older_than_days=None)
    assert req.older_than_days is None

    # 負値は 422
    with pytest.raises(ValidationError) as exc_info:
        StorageCleanupRequest(older_than_days=-1)
    assert "greater than or equal to 0" in str(exc_info.value)


def test_storage_cleanup_response_has_required_fields():
    """StorageCleanupResponse が deleted_podcast_count / deleted_blob_count / freed_bytes を持つこと。"""
    from api.schemas import StorageCleanupResponse

    resp = StorageCleanupResponse(
        deleted_podcast_count=5,
        deleted_blob_count=4,
        freed_bytes=1024000,
    )
    assert resp.deleted_podcast_count == 5
    assert resp.deleted_blob_count == 4
    assert resp.freed_bytes == 1024000


def test_storage_usage_item_has_required_fields():
    """StorageUsageItem が id / type / size_bytes / created_at を持つこと。"""
    from api.schemas import StorageUsageItem

    item = StorageUsageItem(
        id="pod1",
        type="digest",
        size_bytes=512000,
        created_at="2026-06-25T12:00:00+00:00",
    )
    assert item.id == "pod1"
    assert item.type == "digest"
    assert item.size_bytes == 512000
    assert item.created_at == "2026-06-25T12:00:00+00:00"


def test_storage_usage_response_has_required_fields():
    """StorageUsageResponse が total_bytes / podcast_count / items を持つこと。"""
    from api.schemas import StorageUsageResponse, StorageUsageItem

    item1 = StorageUsageItem(
        id="pod1",
        type="digest",
        size_bytes=512000,
        created_at="2026-06-25T12:00:00+00:00",
    )
    item2 = StorageUsageItem(
        id="pod2",
        type="single",
        size_bytes=256000,
        created_at="2026-06-24T12:00:00+00:00",
    )

    resp = StorageUsageResponse(
        total_bytes=768000,
        podcast_count=2,
        items=[item1, item2],
    )
    assert resp.total_bytes == 768000
    assert resp.podcast_count == 2
    assert len(resp.items) == 2
