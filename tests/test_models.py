from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from shared.models import (
    Article,
    Podcast,
    PushSubscription,
    Recommendation,
    RecommendedArticle,
    RssSource,
    UserPrefs,
)

NOW = datetime(2026, 6, 9, 12, 0, 0, tzinfo=timezone.utc)


class TestArticle:
    def test_valid_creation(self):
        article = Article(
            id="abc123",
            title="Test Article",
            url="https://example.com/test",
            source="HackerNews",
            content="Some content here",
            published_at=NOW,
            fetched_at=NOW,
        )
        assert article.id == "abc123"
        assert article.title == "Test Article"
        assert article.content_fetched_at is None

    def test_content_fetched_at_can_be_set(self):
        article = Article(
            id="abc123",
            title="Test Article",
            url="https://example.com/test",
            source="HackerNews",
            content="Some content here",
            published_at=NOW,
            fetched_at=NOW,
            content_fetched_at=NOW,
        )
        assert article.content_fetched_at == NOW

    def test_missing_required_fields_raises(self):
        with pytest.raises(ValidationError):
            Article(id="abc123")

    def test_model_dump_includes_all_fields(self):
        article = Article(
            id="abc123",
            title="Test",
            url="https://example.com",
            source="source",
            content="content",
            published_at=NOW,
            fetched_at=NOW,
        )
        data = article.model_dump()
        for field in ["id", "title", "url", "source", "content", "published_at", "fetched_at", "content_fetched_at"]:
            assert field in data
        assert data["content_fetched_at"] is None


class TestRssSource:
    def test_valid_creation(self):
        source = RssSource(name="HackerNews", url="https://news.ycombinator.com/rss")
        assert source.name == "HackerNews"
        assert source.url == "https://news.ycombinator.com/rss"

    def test_missing_url_raises(self):
        with pytest.raises(ValidationError):
            RssSource(name="HackerNews")

    def test_missing_name_raises(self):
        with pytest.raises(ValidationError):
            RssSource(url="https://news.ycombinator.com/rss")


class TestUserPrefs:
    def test_valid_creation_with_defaults(self):
        prefs = UserPrefs(user_id="user1", default_difficulty="toeic_900")
        assert prefs.starred_article_ids == []
        assert prefs.dismissed_article_ids == []
        assert prefs.rss_sources == []
        assert prefs.default_playback_speed == 1.0
        assert prefs.digest_enabled is True
        assert prefs.digest_article_count == 5

    def test_rss_sources_as_nested_models(self):
        prefs = UserPrefs(
            user_id="user1",
            default_difficulty="toeic_600",
            rss_sources=[{"name": "HackerNews", "url": "https://news.ycombinator.com/rss"}],
        )
        assert len(prefs.rss_sources) == 1
        assert isinstance(prefs.rss_sources[0], RssSource)
        assert prefs.rss_sources[0].name == "HackerNews"

    def test_custom_playback_speed(self):
        prefs = UserPrefs(user_id="user1", default_difficulty="ielts_7", default_playback_speed=1.5)
        assert prefs.default_playback_speed == 1.5

    def test_starred_and_dismissed_ids(self):
        prefs = UserPrefs(
            user_id="user1",
            default_difficulty="eiken_p1",
            starred_article_ids=["art1", "art2"],
            dismissed_article_ids=["art3"],
        )
        assert prefs.starred_article_ids == ["art1", "art2"]
        assert prefs.dismissed_article_ids == ["art3"]

    def test_missing_user_id_raises(self):
        with pytest.raises(ValidationError):
            UserPrefs(default_difficulty="toeic_900")

    def test_read_article_ids_default_empty_list(self):
        """read_article_ids はデフォルト空リスト（後方互換）。"""
        prefs = UserPrefs(user_id="user1", default_difficulty="toeic_900")
        assert prefs.read_article_ids == []
        assert isinstance(prefs.read_article_ids, list)

    def test_read_article_ids_can_be_set(self):
        """read_article_ids に値を設定できる（dismissed_article_ids と同形）。"""
        prefs = UserPrefs(
            user_id="user1",
            default_difficulty="toeic_900",
            read_article_ids=["art1", "art2"],
        )
        assert prefs.read_article_ids == ["art1", "art2"]

    def test_backward_compat_read_article_ids_in_model_dump(self):
        """既存 Firestore ドキュメント（read_article_ids なし）も後方互換。"""
        prefs = UserPrefs(user_id="user1", default_difficulty="toeic_900")
        data = prefs.model_dump()
        assert "read_article_ids" in data
        assert data["read_article_ids"] == []


class TestRecommendedArticle:
    def test_valid_creation(self):
        rec_article = RecommendedArticle(article_id="art1", score=0.95)
        assert rec_article.article_id == "art1"
        assert rec_article.score == 0.95

    def test_missing_fields_raises(self):
        with pytest.raises(ValidationError):
            RecommendedArticle(article_id="art1")


class TestRecommendation:
    def test_valid_creation(self):
        rec = Recommendation(
            user_id="user1",
            date="2026-06-09",
            articles=[{"article_id": "art1", "score": 0.95}],
            generated_at=NOW,
        )
        assert rec.user_id == "user1"
        assert rec.date == "2026-06-09"
        assert len(rec.articles) == 1
        assert isinstance(rec.articles[0], RecommendedArticle)
        assert rec.articles[0].score == 0.95

    def test_articles_default_to_empty(self):
        rec = Recommendation(user_id="user1", date="2026-06-09", generated_at=NOW)
        assert rec.articles == []

    def test_missing_required_fields_raises(self):
        with pytest.raises(ValidationError):
            Recommendation(user_id="user1")


class TestPodcast:
    def _build(self, **overrides):
        defaults = dict(
            id="pod1",
            type="single",
            article_ids=["art1"],
            difficulty="toeic_900",
            audio_url="https://storage.example.com/pod1.mp3",
            japanese_intro_text="日本語のイントロ",
            duration_seconds=300,
            status="completed",
            created_at=NOW,
            user_id="user1",
        )
        defaults.update(overrides)
        return Podcast(**defaults)

    def test_valid_single_type(self):
        podcast = self._build()
        assert podcast.type == "single"
        assert podcast.status == "completed"
        assert podcast.error_message is None

    def test_valid_digest_type(self):
        podcast = self._build(type="digest", article_ids=["art1", "art2", "art3"])
        assert podcast.type == "digest"
        assert len(podcast.article_ids) == 3

    def test_all_valid_statuses(self):
        for status in ["processing", "completed", "failed", "partial_failed"]:
            podcast = self._build(status=status)
            assert podcast.status == status

    def test_invalid_type_raises(self):
        with pytest.raises(ValidationError):
            self._build(type="invalid")

    def test_invalid_status_raises(self):
        with pytest.raises(ValidationError):
            self._build(status="unknown_status")

    def test_invalid_difficulty_raises(self):
        """script_generator._DIFFICULTY_INSTRUCTIONS に存在しない文字列は拒否される。"""
        with pytest.raises(ValidationError):
            self._build(difficulty="intermediate")

    def test_all_valid_difficulties(self):
        """DifficultyLevel の全バリアントが Podcast で使えること。"""
        for difficulty in ["toeic_600", "toeic_900", "ielts_55", "ielts_7", "eiken_2", "eiken_p1"]:
            podcast = self._build(difficulty=difficulty)
            assert podcast.difficulty == difficulty

    def test_invalid_default_difficulty_raises(self):
        """UserPrefs.default_difficulty も DifficultyLevel を使うこと。"""
        with pytest.raises(ValidationError):
            UserPrefs(user_id="user1", default_difficulty="beginner")

    def test_error_message_set_on_failure(self):
        podcast = self._build(status="failed", audio_url="", duration_seconds=0, error_message="TTS API timeout")
        assert podcast.error_message == "TTS API timeout"

    def test_model_dump_includes_all_fields(self):
        podcast = self._build()
        data = podcast.model_dump()
        for field in [
            "id", "type", "article_ids", "difficulty", "audio_url",
            "japanese_intro_text", "duration_seconds", "status",
            "error_message", "created_at", "user_id",
        ]:
            assert field in data


class TestPushSubscription:
    def test_valid_creation_with_defaults(self):
        sub = PushSubscription(
            user_id="user1",
            endpoint="https://push.example.com/endpoint/abc123",
            p256dh="base64_encoded_key",
            auth="base64_encoded_auth",
            created_at=NOW,
        )
        assert sub.user_id == "user1"
        assert sub.endpoint == "https://push.example.com/endpoint/abc123"
        assert sub.p256dh == "base64_encoded_key"
        assert sub.auth == "base64_encoded_auth"
        assert sub.platform == "webpush"
        assert sub.created_at == NOW

    def test_platform_defaults_to_webpush(self):
        sub = PushSubscription(
            user_id="user1",
            endpoint="https://push.example.com/endpoint/abc123",
            p256dh="key",
            auth="auth",
            created_at=NOW,
        )
        assert sub.platform == "webpush"

    def test_all_required_fields(self):
        with pytest.raises(ValidationError):
            PushSubscription(
                user_id="user1",
                endpoint="https://push.example.com/endpoint/abc123",
                p256dh="key",
            )
