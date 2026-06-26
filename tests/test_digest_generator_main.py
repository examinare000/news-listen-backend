"""日次ダイジェスト生成ジョブのテスト。

T1: select_digest_articles（純粋関数）
T2: script_generator._parse_script 抽出 + generate_digest
T3: _build_digest_podcast ビルダー
T4: main() フロー統合
"""
from datetime import datetime, timezone

import pytest

from shared.models import Article, Recommendation, RecommendedArticle, UserPrefs


NOW = datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
TODAY = "2026-06-25"
USER_ID = "test_user"


def _make_article(article_id: str, title: str = "Test Article") -> Article:
    """テスト用記事ファクトリ。"""
    return Article(
        id=article_id,
        title=title,
        url=f"https://example.com/{article_id}",
        source="test-source",
        content=f"Content for {article_id}",
        published_at=NOW,
        fetched_at=NOW,
    )


# ──────────────────────────────────────────────
# T1: select_digest_articles（純粋関数）
# ──────────────────────────────────────────────


class TestSelectDigestArticles:
    """starred ∩ recommendation.articles を score 降順で選定する。"""

    def test_select_basic_starred_and_recommended(self):
        """starred と recommendation に共通する記事を score 降順で返す。"""
        from jobs.digest_generator.main import select_digest_articles

        starred_ids = {"article1", "article2", "article3"}
        articles_by_id = {
            "article1": _make_article("article1"),
            "article2": _make_article("article2"),
            "article3": _make_article("article3"),
            "article4": _make_article("article4"),
        }
        rec = Recommendation(
            user_id=USER_ID,
            date=TODAY,
            articles=[
                RecommendedArticle(article_id="article2", score=0.9),
                RecommendedArticle(article_id="article1", score=0.8),
                RecommendedArticle(article_id="article4", score=0.7),  # starred に無い
            ],
            generated_at=NOW,
        )

        result = select_digest_articles(
            starred_ids=starred_ids,
            recommendation=rec,
            articles_by_id=articles_by_id,
            count=5,
        )

        # starred ∩ rec.articles = {article1, article2}
        # score 降順: article2 (0.9) → article1 (0.8)
        assert len(result) == 2
        assert result[0].id == "article2"
        assert result[1].id == "article1"

    def test_select_clamps_count_to_min_3(self):
        """count < 3 の場合、3 にクランプされること。"""
        from jobs.digest_generator.main import select_digest_articles

        starred_ids = {"a1", "a2", "a3", "a4", "a5"}
        articles_by_id = {f"a{i}": _make_article(f"a{i}") for i in range(1, 6)}
        rec = Recommendation(
            user_id=USER_ID,
            date=TODAY,
            articles=[
                RecommendedArticle(article_id=f"a{i}", score=float(10 - i))
                for i in range(1, 6)
            ],
            generated_at=NOW,
        )

        # count=2 → クランプで 3
        result = select_digest_articles(
            starred_ids=starred_ids,
            recommendation=rec,
            articles_by_id=articles_by_id,
            count=2,
        )

        assert len(result) == 3

    def test_select_clamps_count_to_max_10(self):
        """count > 10 の場合、10 にクランプされること。"""
        from jobs.digest_generator.main import select_digest_articles

        starred_ids = {f"a{i}" for i in range(1, 20)}
        articles_by_id = {f"a{i}": _make_article(f"a{i}") for i in range(1, 20)}
        rec = Recommendation(
            user_id=USER_ID,
            date=TODAY,
            articles=[
                RecommendedArticle(article_id=f"a{i}", score=float(20 - i))
                for i in range(1, 20)
            ],
            generated_at=NOW,
        )

        # count=15 → クランプで 10
        result = select_digest_articles(
            starred_ids=starred_ids,
            recommendation=rec,
            articles_by_id=articles_by_id,
            count=15,
        )

        assert len(result) == 10

    def test_select_empty_recommendation_returns_empty(self):
        """recommendation が None なら空リストを返す。"""
        from jobs.digest_generator.main import select_digest_articles

        starred_ids = {"a1", "a2"}
        articles_by_id = {"a1": _make_article("a1"), "a2": _make_article("a2")}

        result = select_digest_articles(
            starred_ids=starred_ids,
            recommendation=None,
            articles_by_id=articles_by_id,
            count=5,
        )

        assert result == []

    def test_select_skips_unresolved_article_ids(self):
        """articles_by_id に存在しない article_id はスキップされること。"""
        from jobs.digest_generator.main import select_digest_articles

        starred_ids = {"a1", "a2", "a3"}
        articles_by_id = {
            "a1": _make_article("a1"),
            "a2": _make_article("a2"),
            # a3 は articles_by_id に無い
        }
        rec = Recommendation(
            user_id=USER_ID,
            date=TODAY,
            articles=[
                RecommendedArticle(article_id="a2", score=0.9),
                RecommendedArticle(article_id="a3", score=0.8),
                RecommendedArticle(article_id="a1", score=0.7),
            ],
            generated_at=NOW,
        )

        result = select_digest_articles(
            starred_ids=starred_ids,
            recommendation=rec,
            articles_by_id=articles_by_id,
            count=5,
        )

        # a3 は未解決 → スキップ
        # 返: a2 (0.9), a1 (0.7)
        assert len(result) == 2
        assert result[0].id == "a2"
        assert result[1].id == "a1"

    def test_select_stable_sort(self):
        """同一 score の記事は入力順を保つ（安定ソート）。"""
        from jobs.digest_generator.main import select_digest_articles

        starred_ids = {"a1", "a2", "a3"}
        articles_by_id = {f"a{i}": _make_article(f"a{i}") for i in range(1, 4)}
        rec = Recommendation(
            user_id=USER_ID,
            date=TODAY,
            articles=[
                RecommendedArticle(article_id="a1", score=0.5),
                RecommendedArticle(article_id="a2", score=0.5),
                RecommendedArticle(article_id="a3", score=0.5),
            ],
            generated_at=NOW,
        )

        result = select_digest_articles(
            starred_ids=starred_ids,
            recommendation=rec,
            articles_by_id=articles_by_id,
            count=5,
        )

        # score が全て 0.5 → 入力順 (a1, a2, a3)
        assert [a.id for a in result] == ["a1", "a2", "a3"]

    def test_select_zero_articles_returns_empty(self):
        """starred と recommendation に共通要素が無い場合、空リストを返す。"""
        from jobs.digest_generator.main import select_digest_articles

        starred_ids = {"a1"}
        articles_by_id = {
            "a1": _make_article("a1"),
            "b1": _make_article("b1"),
        }
        rec = Recommendation(
            user_id=USER_ID,
            date=TODAY,
            articles=[
                RecommendedArticle(article_id="b1", score=0.9),
            ],
            generated_at=NOW,
        )

        result = select_digest_articles(
            starred_ids=starred_ids,
            recommendation=rec,
            articles_by_id=articles_by_id,
            count=5,
        )

        assert result == []


# ──────────────────────────────────────────────
# T2: script_generator._parse_script + generate_digest
# ──────────────────────────────────────────────


class TestScriptGeneratorParseScript:
    """_parse_script 抽出（非回帰・既存 generate パス互換）。"""

    def test_parse_script_correct_format(self):
        """===JAPANESE_INTRO===...===ENGLISH_BODY===... 形式をパースする。"""
        from unittest.mock import MagicMock
        from jobs.podcast_generator.script_generator import ScriptGenerator

        mock_gemini = MagicMock()
        gen = ScriptGenerator(gemini_client=mock_gemini)
        raw = """\
===JAPANESE_INTRO===
日本語イントロ本文

===ENGLISH_BODY===
English body text here"""

        result = gen._parse_script(raw)

        assert result.japanese_intro == "日本語イントロ本文"
        assert result.english_body == "English body text here"

    def test_parse_script_missing_format_uses_raw_as_body(self):
        """形式が無い場合、生スクリプトを body に割り当てる。"""
        from unittest.mock import MagicMock
        from jobs.podcast_generator.script_generator import ScriptGenerator

        mock_gemini = MagicMock()
        gen = ScriptGenerator(gemini_client=mock_gemini)
        raw = "Random script without markers"

        result = gen._parse_script(raw)

        assert result.japanese_intro == ""
        assert result.english_body == "Random script without markers"

    def test_parse_script_strips_whitespace(self):
        """intro・body の前後空白は strip される。"""
        from unittest.mock import MagicMock
        from jobs.podcast_generator.script_generator import ScriptGenerator

        mock_gemini = MagicMock()
        gen = ScriptGenerator(gemini_client=mock_gemini)
        raw = """\
===JAPANESE_INTRO===

  インストロ

===ENGLISH_BODY===

Body text  """

        result = gen._parse_script(raw)

        assert result.japanese_intro == "インストロ"
        assert result.english_body == "Body text"


class TestScriptGeneratorDigest:
    """generate_digest（複数記事用プロンプト）。"""

    def test_generate_digest_calls_gemini_with_articles(self):
        """複数記事を含むプロンプトで Gemini を呼ぶ。"""
        from unittest.mock import MagicMock
        from jobs.podcast_generator.script_generator import ScriptGenerator

        mock_gemini = MagicMock()
        mock_gemini.generate_text.return_value = """\
===JAPANESE_INTRO===
本日のダイジェスト

===ENGLISH_BODY===
Today's digest content"""

        gen = ScriptGenerator(gemini_client=mock_gemini)
        articles = [_make_article("a1"), _make_article("a2")]

        result = gen.generate_digest(articles, "toeic_900", TODAY)

        # Gemini が呼ばれたこと
        mock_gemini.generate_text.assert_called_once()
        prompt = mock_gemini.generate_text.call_args[0][0]

        # プロンプトに複数記事が含まれることを確認
        assert "a1" in prompt
        assert "a2" in prompt
        assert "toeic_900" not in prompt  # 難易度は _DIFFICULTY_INSTRUCTIONS から解決される
        assert TODAY in prompt

        # 返り値は PodcastScript
        assert result.japanese_intro == "本日のダイジェスト"
        assert result.english_body == "Today's digest content"

    def test_generate_digest_uses_parse_script(self):
        """generate_digest は _parse_script を通じて出力をパースする。"""
        from unittest.mock import MagicMock
        from jobs.podcast_generator.script_generator import ScriptGenerator

        mock_gemini = MagicMock()
        mock_gemini.generate_text.return_value = """\
===JAPANESE_INTRO===
日本語

===ENGLISH_BODY===
English"""

        gen = ScriptGenerator(gemini_client=mock_gemini)
        articles = [_make_article("a1")]

        result = gen.generate_digest(articles, "toeic_900", TODAY)

        assert result.japanese_intro == "日本語"
        assert result.english_body == "English"

    def test_generate_digest_with_empty_articles_list(self):
        """空の記事リストでも呼び出し可能。"""
        from unittest.mock import MagicMock
        from jobs.podcast_generator.script_generator import ScriptGenerator

        mock_gemini = MagicMock()
        mock_gemini.generate_text.return_value = """\
===JAPANESE_INTRO===
Empty

===ENGLISH_BODY===
No articles"""

        gen = ScriptGenerator(gemini_client=mock_gemini)

        result = gen.generate_digest([], "toeic_900", TODAY)

        assert result.japanese_intro == "Empty"
        assert result.english_body == "No articles"

    def test_generate_existing_behavior_not_regressed(self):
        """既存 generate メソッドは _parse_script リファクタ後も変わらないこと。"""
        from unittest.mock import MagicMock
        from jobs.podcast_generator.script_generator import ScriptGenerator

        mock_gemini = MagicMock()
        mock_gemini.generate_text.return_value = """\
===JAPANESE_INTRO===
既存形式イントロ

===ENGLISH_BODY===
既存形式本編"""

        gen = ScriptGenerator(gemini_client=mock_gemini)
        main_article = _make_article("main")
        related_articles = [_make_article("related")]

        result = gen.generate(main_article, related_articles, "toeic_900", TODAY)

        # 既存のパース結果と同じこと
        assert result.japanese_intro == "既存形式イントロ"
        assert result.english_body == "既存形式本編"


# ──────────────────────────────────────────────
# T3: _build_digest_podcast（ビルダー関数）
# ──────────────────────────────────────────────


class TestBuildDigestPodcast:
    """digest 専用 Podcast ビルダー。"""

    def test_build_digest_podcast_basic_fields(self):
        """digest_id・type・article_ids などの基本フィールドが正しく設定されること。"""
        from jobs.digest_generator.main import _build_digest_podcast

        article_ids = ["a1", "a2", "a3"]
        result = _build_digest_podcast(
            user_id=USER_ID,
            digest_id="test_user_2026-06-25_digest",
            article_ids=article_ids,
            audio_url="gs://bucket/path/digest.mp3",
            japanese_intro_text="本日のダイジェスト",
            duration_seconds=300,
        )

        assert result.id == "test_user_2026-06-25_digest"
        assert result.type == "digest"
        assert result.article_ids == article_ids
        assert result.user_id == USER_ID

    def test_build_digest_podcast_completed_status(self):
        """デフォルト status は "completed"。"""
        from jobs.digest_generator.main import _build_digest_podcast

        result = _build_digest_podcast(
            user_id=USER_ID,
            digest_id="test_digest",
            article_ids=["a1"],
            audio_url="gs://bucket/audio.mp3",
            japanese_intro_text="Intro",
            duration_seconds=100,
        )

        assert result.status == "completed"
        assert result.error_message is None

    def test_build_digest_podcast_partial_failed_status(self):
        """status="partial_failed"・error_message を設定可能。"""
        from jobs.digest_generator.main import _build_digest_podcast

        result = _build_digest_podcast(
            user_id=USER_ID,
            digest_id="test_digest",
            article_ids=["a1", "a2"],
            audio_url="gs://bucket/audio.mp3",
            japanese_intro_text="Intro",
            duration_seconds=100,
            status="partial_failed",
            error_message="Segment 2 failed to generate",
        )

        assert result.status == "partial_failed"
        assert result.error_message == "Segment 2 failed to generate"

    def test_build_digest_podcast_audio_and_intro_text(self):
        """audio_url・japanese_intro_text が正しく設定されること。"""
        from jobs.digest_generator.main import _build_digest_podcast

        audio_url = "gs://bucket/digest_audio.mp3"
        intro_text = "日本語イントロ本文"

        result = _build_digest_podcast(
            user_id=USER_ID,
            digest_id="test_digest",
            article_ids=["a1"],
            audio_url=audio_url,
            japanese_intro_text=intro_text,
            duration_seconds=200,
        )

        assert result.audio_url == audio_url
        assert result.japanese_intro_text == intro_text

    def test_build_digest_podcast_duration(self):
        """duration_seconds が正しく設定されること。"""
        from jobs.digest_generator.main import _build_digest_podcast

        result = _build_digest_podcast(
            user_id=USER_ID,
            digest_id="test_digest",
            article_ids=["a1"],
            audio_url="gs://bucket/audio.mp3",
            japanese_intro_text="Intro",
            duration_seconds=550,
        )

        assert result.duration_seconds == 550

    def test_build_digest_podcast_created_at_is_set(self):
        """created_at が現在時刻に設定されること。"""
        from jobs.digest_generator.main import _build_digest_podcast

        before = datetime.now(timezone.utc)
        result = _build_digest_podcast(
            user_id=USER_ID,
            digest_id="test_digest",
            article_ids=["a1"],
            audio_url="gs://bucket/audio.mp3",
            japanese_intro_text="Intro",
            duration_seconds=100,
        )
        after = datetime.now(timezone.utc)

        assert before <= result.created_at <= after

    def test_build_digest_podcast_playback_position_zero(self):
        """playback_position_seconds は 0.0。"""
        from jobs.digest_generator.main import _build_digest_podcast

        result = _build_digest_podcast(
            user_id=USER_ID,
            digest_id="test_digest",
            article_ids=["a1"],
            audio_url="gs://bucket/audio.mp3",
            japanese_intro_text="Intro",
            duration_seconds=100,
        )

        assert result.playback_position_seconds == 0.0


# ──────────────────────────────────────────────
# T4: main() フロー統合
# ──────────────────────────────────────────────


def _make_prefs(
    digest_enabled: bool = True,
    digest_article_count: int = 5,
    starred_ids: list[str] | None = None,
) -> UserPrefs:
    """テスト用 UserPrefs ファクトリ。"""
    from shared.models import UserPrefs

    if starred_ids is None:
        starred_ids = ["a1", "a2", "a3"]

    return UserPrefs(
        user_id=USER_ID,
        starred_article_ids=starred_ids,
        default_difficulty="toeic_900",
        digest_enabled=digest_enabled,
        digest_article_count=digest_article_count,
    )


def _make_recommendation(article_ids: list[str] | None = None) -> Recommendation:
    """テスト用 Recommendation ファクトリ。"""
    if article_ids is None:
        article_ids = ["a1", "a2", "a3"]

    return Recommendation(
        user_id=USER_ID,
        date=TODAY,
        articles=[
            RecommendedArticle(article_id=aid, score=0.9 - (i * 0.1))
            for i, aid in enumerate(article_ids)
        ],
        generated_at=NOW,
    )


_BASE_DIGEST_ENV = {
    "USER_ID": USER_ID,
    "GCS_BUCKET_NAME": "test-bucket",
    "GEMINI_API_KEY": "test-key",
}


@pytest.fixture
def digest_mocks():
    """main() の全外部依存をモック化する。"""
    from unittest.mock import patch

    with patch.dict("os.environ", _BASE_DIGEST_ENV), \
         patch("jobs.digest_generator.main.FirestoreClient") as MockDb, \
         patch("jobs.digest_generator.main.StorageClient") as MockStorage, \
         patch("shared.gemini_client.GeminiClient"), \
         patch("jobs.podcast_generator.script_generator.ScriptGenerator") as MockScriptGen, \
         patch("jobs.podcast_generator.tts_generator.TtsGenerator") as MockTtsGen, \
         patch("jobs.digest_generator.main.datetime") as MockDateTime:

        # 実クロック依存を排除し、テストを実行日に対して決定的にする（NOW/TODAY 定数の意図）
        MockDateTime.now.return_value = NOW

        db = MockDb.return_value
        storage = MockStorage.return_value
        script_gen = MockScriptGen.return_value
        tts_gen = MockTtsGen.return_value

        # デフォルトの振る舞い
        db.get_user_prefs.return_value = _make_prefs()
        db.get_recommendation.return_value = _make_recommendation()
        db.get_recent_articles.return_value = [
            _make_article("a1"),
            _make_article("a2"),
            _make_article("a3"),
        ]
        db.get_podcast.return_value = None  # 冪等チェック: 既存 digest なし

        from jobs.podcast_generator.script_generator import PodcastScript
        from jobs.podcast_generator.tts_generator import TtsResult

        script_gen.generate_digest.return_value = PodcastScript(
            japanese_intro="日次ダイジェスト",
            english_body="Daily digest content",
        )

        # TTS: 300 秒分の音声（48000 bytes/sec * 300 = 14400000 bytes）
        tts_gen.generate_audio.return_value = TtsResult(
            audio=b"x" * (48_000 * 300),
            failed_segments=[],
            error_message=None,
        )

        storage.upload_cached_audio.return_value = "gs://bucket/digest.mp3"

        import jobs.digest_generator.main as m

        yield {
            "db": db,
            "storage": storage,
            "script_gen": script_gen,
            "tts_gen": tts_gen,
            "main": m,
        }


class TestMainDigestFlow:
    """main() フロー統合テスト。"""

    def test_main_digest_enabled_false_skips_generation(self, digest_mocks):
        """digest_enabled=False なら生成しずに終了。"""
        db = digest_mocks["db"]
        db.get_user_prefs.return_value = _make_prefs(digest_enabled=False)

        digest_mocks["main"].main()

        # save_podcast が呼ばれていないこと
        db.save_podcast.assert_not_called()

    def test_main_existing_digest_skips_regeneration(self, digest_mocks):
        """既存 digest があれば冪等スキップ。"""
        db = digest_mocks["db"]
        from shared.models import Podcast

        # 既存 digest が存在
        db.get_podcast.return_value = Podcast(
            id=f"{USER_ID}_{TODAY}_digest",
            type="digest",
            article_ids=["a1", "a2"],
            difficulty="toeic_900",
            audio_url="gs://bucket/old.mp3",
            japanese_intro_text="Old",
            duration_seconds=100,
            status="completed",
            created_at=NOW,
            user_id=USER_ID,
        )

        digest_mocks["main"].main()

        # save_podcast が呼ばれていないこと
        db.save_podcast.assert_not_called()

    def test_main_zero_articles_selected_skips_generation(self, digest_mocks):
        """選定記事が 0 件なら生成しずに終了。"""
        db = digest_mocks["db"]
        # starred と recommendation に共通記事なし
        db.get_user_prefs.return_value = _make_prefs(starred_ids=["x1"])
        db.get_recommendation.return_value = _make_recommendation(["a1"])

        digest_mocks["main"].main()

        # save_podcast が呼ばれていないこと
        db.save_podcast.assert_not_called()

    def test_main_success_saves_digest_podcast(self, digest_mocks):
        """正常系: digest Podcast を save_podcast で保存。"""
        db = digest_mocks["db"]

        digest_mocks["main"].main()

        # save_podcast が呼ばれたこと
        db.save_podcast.assert_called_once()
        saved = db.save_podcast.call_args[0][0]

        assert saved.type == "digest"
        assert saved.user_id == USER_ID
        assert len(saved.article_ids) > 0

    def test_main_success_digest_id_is_deterministic(self, digest_mocks):
        """digest_id が決定論的（user_id_YYYY-MM-DD_digest）。"""
        db = digest_mocks["db"]

        digest_mocks["main"].main()

        saved = db.save_podcast.call_args[0][0]
        expected_digest_id = f"{USER_ID}_{TODAY}_digest"
        assert saved.id == expected_digest_id

    def test_main_success_article_ids_from_selection(self, digest_mocks):
        """article_ids は select_digest_articles の返り値から。"""
        db = digest_mocks["db"]

        digest_mocks["main"].main()

        saved = db.save_podcast.call_args[0][0]
        # デフォルト prefs: starred=[a1, a2, a3], recommendation=[a1, a2, a3]
        # select 結果: score 降順で最大 5 件 → [a1, a2, a3]（正確には recommendation から）
        assert set(saved.article_ids).issubset({"a1", "a2", "a3"})

    def test_main_success_status_completed(self, digest_mocks):
        """正常系では status="completed"。"""
        db = digest_mocks["db"]

        digest_mocks["main"].main()

        saved = db.save_podcast.call_args[0][0]
        assert saved.status == "completed"
        assert saved.error_message is None

    def test_main_tts_partial_failed_saves_partial_status(self, digest_mocks):
        """TTS が partial_failed（failed_segments 非空）なら status="partial_failed"。"""
        from jobs.podcast_generator.tts_generator import TtsResult

        db = digest_mocks["db"]
        digest_mocks["tts_gen"].generate_audio.return_value = TtsResult(
            audio=b"x" * 48_000,
            failed_segments=["segment_2"],
            error_message="Segment 2 TTS failed",
        )

        digest_mocks["main"].main()

        saved = db.save_podcast.call_args[0][0]
        assert saved.status == "partial_failed"
        assert "Segment 2 TTS failed" in saved.error_message

    def test_main_tts_full_failure_skips_save(self, digest_mocks):
        """TTS が全失敗（TtsGenerationError）なら Podcast 保存せず終了。"""
        from jobs.podcast_generator.tts_generator import TtsGenerationError

        db = digest_mocks["db"]
        digest_mocks["tts_gen"].generate_audio.side_effect = TtsGenerationError(
            "TTS generation failed"
        )

        digest_mocks["main"].main()

        # save_podcast が呼ばれていないこと
        db.save_podcast.assert_not_called()

    def test_main_success_calls_script_gen_digest(self, digest_mocks):
        """script_gen.generate_digest が呼ばれること（複数記事用）。"""
        script_gen = digest_mocks["script_gen"]

        digest_mocks["main"].main()

        # generate_digest が呼ばれたこと
        script_gen.generate_digest.assert_called_once()

        # call_args は (args, kwargs) の tuple
        call_args, call_kwargs = script_gen.generate_digest.call_args

        # positional args: articles, difficulty, date_str
        if call_args:
            articles_arg = call_args[0]
            difficulty_arg = call_args[1] if len(call_args) > 1 else call_kwargs.get("difficulty")
            date_arg = call_args[2] if len(call_args) > 2 else call_kwargs.get("date_str")
        else:
            articles_arg = call_kwargs.get("articles")
            difficulty_arg = call_kwargs.get("difficulty")
            date_arg = call_kwargs.get("date_str")

        # articles: Article リスト
        assert all(hasattr(a, "id") for a in articles_arg)
        # difficulty: toeic_900
        assert difficulty_arg == "toeic_900"
        # date: TODAY
        assert date_arg == TODAY

    def test_main_success_uploads_audio(self, digest_mocks):
        """storage.upload_cached_audio が呼ばれること。"""
        storage = digest_mocks["storage"]

        digest_mocks["main"].main()

        storage.upload_cached_audio.assert_called_once()

    def test_main_success_audio_url_from_storage(self, digest_mocks):
        """Podcast.audio_url は storage.upload_cached_audio の戻り値。"""
        db = digest_mocks["db"]
        storage = digest_mocks["storage"]
        storage.upload_cached_audio.return_value = "gs://bucket/custom_digest.mp3"

        digest_mocks["main"].main()

        saved = db.save_podcast.call_args[0][0]
        assert saved.audio_url == "gs://bucket/custom_digest.mp3"

    def test_main_success_sends_notification(self, digest_mocks):
        """通知が送信されること。"""
        from unittest.mock import MagicMock

        notifier = MagicMock()

        digest_mocks["main"].main(notifier=notifier)

        # notifier.notify_completion が呼ばれたこと
        notifier.notify_completion.assert_called_once()
        call_kwargs = notifier.notify_completion.call_args[1]
        assert call_kwargs["user_id"] == USER_ID
        assert "データ" in call_kwargs["title"] or "ダイジェスト" in call_kwargs["title"]

    def test_main_notification_failure_does_not_break_job(self, digest_mocks):
        """通知送信失敗はジョブ成功に影響しない。"""
        from unittest.mock import MagicMock

        db = digest_mocks["db"]
        notifier = MagicMock()
        notifier.notify_completion.side_effect = Exception("Notification failed")

        # 例外が出ずに終了すること
        digest_mocks["main"].main(notifier=notifier)

        # save_podcast は呼ばれたこと
        db.save_podcast.assert_called_once()
