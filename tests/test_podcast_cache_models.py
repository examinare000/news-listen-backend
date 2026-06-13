"""PodcastCache モデル・CacheStatus・DEFAULT_PODCAST_LANGUAGE のユニットテスト。

CacheStatus を PodcastStatus と別型にする設計判断（partial_failed を含まない）と、
DEFAULT_PODCAST_LANGUAGE 定数の存在を検証する。
"""
from datetime import datetime, timezone
from typing import get_args

import pytest
from pydantic import ValidationError

NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)


class TestDefaultPodcastLanguage:
    def test_default_podcast_language_is_ja_en(self):
        """DEFAULT_PODCAST_LANGUAGE は "ja-en" であること（main.py へのマジック文字列直書きを防ぐ）。"""
        from shared.models import DEFAULT_PODCAST_LANGUAGE

        assert DEFAULT_PODCAST_LANGUAGE == "ja-en"

    def test_default_podcast_language_is_string(self):
        from shared.models import DEFAULT_PODCAST_LANGUAGE

        assert isinstance(DEFAULT_PODCAST_LANGUAGE, str)


class TestCacheStatus:
    def test_cache_status_does_not_include_partial_failed(self):
        """CacheStatus は 'partial_failed' を含まない。

        PodcastStatus は partial_failed を持つが、キャッシュには存在しない概念のため
        別 Literal 型にすることで型レベルで混入を防ぐ。
        """
        from shared.models import CacheStatus

        assert "partial_failed" not in get_args(CacheStatus)

    def test_cache_status_includes_processing(self):
        from shared.models import CacheStatus

        assert "processing" in get_args(CacheStatus)

    def test_cache_status_includes_completed(self):
        from shared.models import CacheStatus

        assert "completed" in get_args(CacheStatus)

    def test_cache_status_includes_failed(self):
        from shared.models import CacheStatus

        assert "failed" in get_args(CacheStatus)

    def test_cache_status_has_exactly_three_values(self):
        """CacheStatus は processing / completed / failed の 3 値のみ。余計な値が混入していない。"""
        from shared.models import CacheStatus

        assert len(get_args(CacheStatus)) == 3


class TestPodcastCache:
    def _build(self, **overrides):
        defaults = dict(
            cache_key="art1abc123456789ab__toeic_900__ja-en",
            article_id="art1abc123456789ab",
            difficulty="toeic_900",
            language="ja-en",
            status="processing",
            audio_url=None,
            japanese_intro_text=None,
            duration_seconds=None,
            created_at=NOW,
        )
        defaults.update(overrides)
        from shared.models import PodcastCache

        return PodcastCache(**defaults)

    def test_valid_processing_creation(self):
        """processing 状態では成果物フィールド（audio_url 等）が None でも有効。

        processing 確保時点では成果物が未確定なため Optional とする設計。
        """
        cache = self._build()

        assert cache.status == "processing"
        assert cache.audio_url is None
        assert cache.japanese_intro_text is None
        assert cache.duration_seconds is None

    def test_valid_completed_creation(self):
        """completed 状態では成果物フィールドが全て設定されること。"""
        cache = self._build(
            status="completed",
            audio_url="podcasts/cache/art1abc123456789ab__toeic_900__ja-en.mp3",
            japanese_intro_text="イントロテキスト",
            duration_seconds=300,
        )

        assert cache.status == "completed"
        assert cache.audio_url == "podcasts/cache/art1abc123456789ab__toeic_900__ja-en.mp3"
        assert cache.japanese_intro_text == "イントロテキスト"
        assert cache.duration_seconds == 300

    def test_valid_failed_creation(self):
        cache = self._build(status="failed")

        assert cache.status == "failed"

    def test_invalid_status_partial_failed_raises(self):
        """partial_failed は CacheStatus に含まれないため ValidationError になること。"""
        with pytest.raises(ValidationError):
            self._build(status="partial_failed")

    def test_invalid_status_unknown_raises(self):
        with pytest.raises(ValidationError):
            self._build(status="unknown_status")

    def test_invalid_difficulty_raises(self):
        """DifficultyLevel 外の文字列は Pydantic が拒否すること。"""
        with pytest.raises(ValidationError):
            self._build(difficulty="invalid_level")

    def test_all_valid_difficulties_accepted(self):
        """DifficultyLevel の全バリアントが PodcastCache で使えること。"""
        for difficulty in ["toeic_600", "toeic_900", "ielts_55", "ielts_7", "eiken_2", "eiken_p1"]:
            cache = self._build(
                difficulty=difficulty,
                cache_key=f"art1__{difficulty}__ja-en",
            )
            assert cache.difficulty == difficulty

    def test_missing_required_fields_raises(self):
        from shared.models import PodcastCache

        with pytest.raises(ValidationError):
            PodcastCache(cache_key="key", article_id="art1")

    def test_model_dump_includes_all_expected_fields(self):
        cache = self._build()
        data = cache.model_dump()

        for field in [
            "cache_key",
            "article_id",
            "difficulty",
            "language",
            "status",
            "audio_url",
            "japanese_intro_text",
            "duration_seconds",
            "created_at",
        ]:
            assert field in data, f"フィールド '{field}' が model_dump に含まれていない"

    def test_model_dump_json_serializes_datetime_as_string(self):
        """model_dump(mode='json') は datetime を ISO 文字列に変換すること。

        save_podcast_cache が Firestore に保存する際に型不整合を防ぐため、
        save_podcast / save_article と同じ mode='json' 流儀を用いる。
        """
        cache = self._build()
        data = cache.model_dump(mode="json")

        assert isinstance(data["created_at"], str)
        assert data["created_at"].startswith("2026-06-14")

    def test_cache_key_stored_in_model(self):
        """cache_key は doc-id としても使うが、モデルフィールドとしても保持すること。"""
        cache = self._build()

        assert cache.cache_key == "art1abc123456789ab__toeic_900__ja-en"
