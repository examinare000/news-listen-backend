"""podcast_generator/main.py のジョブフロー統合テスト。

キャッシュ判定ロジック（cache hit / miss / processing / failed 等）が
正しく配線されていることを検証する。3 モジュール以上（firestore / storage /
script_generator / main）をまたぐデータフローをカバーする。
"""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
ARTICLE_ID = "abc123def456789012"  # SHA-256 hex[:20] 相当の固定値
CACHE_KEY = f"{ARTICLE_ID}__toeic_900__ja-en"
AUDIO_BLOB = f"podcasts/cache/{CACHE_KEY}.mp3"

_BASE_ENV = {
    "USER_ID": "user1",
    "GCS_BUCKET_NAME": "test-bucket",
    "GEMINI_API_KEY": "test-key",
    "DIFFICULTY": "toeic_900",
}


# ──────────────────────────────────────────────
# フィクスチャ用ヘルパー
# ──────────────────────────────────────────────


def _make_article():
    from shared.models import Article

    return Article(
        id=ARTICLE_ID,
        title="Test Article",
        url="https://example.com/test",
        source="hackernews",
        content="Test content",
        published_at=NOW,
        fetched_at=NOW,
    )


def _make_prefs(starred=None):
    from shared.models import UserPrefs

    return UserPrefs(
        user_id="user1",
        default_difficulty="toeic_900",
        starred_article_ids=starred if starred is not None else [ARTICLE_ID],
    )


def _make_completed_cache():
    from shared.models import PodcastCache

    return PodcastCache(
        cache_key=CACHE_KEY,
        article_id=ARTICLE_ID,
        difficulty="toeic_900",
        language="ja-en",
        status="completed",
        audio_url=AUDIO_BLOB,
        japanese_intro_text="キャッシュイントロ",
        duration_seconds=120,
        created_at=NOW,
    )


def _make_processing_cache():
    from shared.models import PodcastCache

    return PodcastCache(
        cache_key=CACHE_KEY,
        article_id=ARTICLE_ID,
        difficulty="toeic_900",
        language="ja-en",
        status="processing",
        created_at=NOW,
    )


def _make_failed_cache():
    from shared.models import PodcastCache

    return PodcastCache(
        cache_key=CACHE_KEY,
        article_id=ARTICLE_ID,
        difficulty="toeic_900",
        language="ja-en",
        status="failed",
        created_at=NOW,
    )


# ──────────────────────────────────────────────
# 共通フィクスチャ
# ──────────────────────────────────────────────


@pytest.fixture
def mocks():
    """podcast_generator/main.py の全外部依存をモック化する。

    各テストは fixture から (db, storage, script_gen, tts_gen, main_module) を受け取り、
    必要に応じて戻り値をオーバーライドしてから main_module.main() を呼ぶ。

    WHY: get_user_podcast_for_article をデフォルト None に設定することで、
    既存テストが promote 機能なしで従来どおり save_podcast へフォールバックする。
    promote_user_podcast は MagicMock（no-op）とし、各テストで必要に応じて検証可能にする。
    """
    from jobs.podcast_generator.script_generator import PodcastScript

    with patch.dict("os.environ", _BASE_ENV), \
         patch("jobs.podcast_generator.main.FirestoreClient") as MockDb, \
         patch("jobs.podcast_generator.main.StorageClient") as MockStorage, \
         patch("jobs.podcast_generator.main.GeminiClient"), \
         patch("jobs.podcast_generator.main.ScriptGenerator") as MockScriptGen, \
         patch("jobs.podcast_generator.main.TtsGenerator") as MockTtsGen:

        db = MockDb.return_value
        storage = MockStorage.return_value
        script_gen = MockScriptGen.return_value
        tts_gen = MockTtsGen.return_value

        # デフォルトの振る舞い（各テストで上書き可能）
        db.get_user_prefs.return_value = _make_prefs()
        db.get_recent_articles.return_value = [_make_article()]
        db.podcast_exists_for_article.return_value = False
        db.get_podcast_cache.return_value = None
        db.try_acquire_cache.return_value = True
        # WHY: 既存 per-user Podcast 行を確認するため get_user_podcast_for_article をデフォルト None に。
        # promote フロー テストで上書き可能。
        db.get_user_podcast_for_article.return_value = None
        # WHY: promote_user_podcast は MagicMock のまま。各テストで動作検証可能。

        from jobs.podcast_generator.tts_generator import TtsResult

        mock_script = PodcastScript(title="", japanese_intro="生成イントロ", english_body="English body.")
        script_gen.generate.return_value = mock_script
        # _PCM_BYTES_PER_SECOND = 48_000 → 1 秒分の音声データ
        tts_gen.generate_audio.return_value = TtsResult(
            audio=b"x" * 48_000,
            failed_segments=[],
            error_message=None,
        )
        storage.upload_cached_audio.return_value = AUDIO_BLOB

        import jobs.podcast_generator.main as m

        yield {
            "db": db,
            "storage": storage,
            "script_gen": script_gen,
            "tts_gen": tts_gen,
            "main": m,
        }


# ──────────────────────────────────────────────
# T1: per-user 冪等性
# ──────────────────────────────────────────────


def test_per_user_idempotency_skips_without_cache_lookup(mocks):
    """当該ユーザーが既に Podcast を保有している場合、キャッシュを参照せずにスキップすること。

    spec §2.4 [1]: per-user 冪等チェックがキャッシュ参照より前段にある。
    """
    db = mocks["db"]
    db.podcast_exists_for_article.return_value = True

    mocks["main"].main()

    db.get_podcast_cache.assert_not_called()
    db.save_podcast.assert_not_called()


# ──────────────────────────────────────────────
# T2: 記事が Firestore に未存在
# ──────────────────────────────────────────────


def test_missing_article_skips_without_generation(mocks):
    """articles_by_id に存在しない記事は warning ログ + skip すること（既存挙動を維持）。"""
    db = mocks["db"]
    db.get_recent_articles.return_value = []  # ARTICLE_ID が含まれない

    mocks["main"].main()

    mocks["script_gen"].generate.assert_not_called()
    db.save_podcast.assert_not_called()


# ──────────────────────────────────────────────
# T3: キャッシュヒット (completed)
# ──────────────────────────────────────────────


def test_cache_hit_completed_does_not_call_gemini_or_tts(mocks):
    """キャッシュが completed の場合、Gemini / TTS を呼ばないこと。

    spec §3.1: キャッシュヒット時は「Gemini/TTS を一切呼ばない」が必須検証。
    """
    mocks["db"].get_podcast_cache.return_value = _make_completed_cache()

    mocks["main"].main()

    mocks["script_gen"].generate.assert_not_called()
    mocks["tts_gen"].generate_audio.assert_not_called()


def test_cache_hit_completed_saves_per_user_podcast(mocks):
    """キャッシュヒット時、per-user Podcast を 1 件作成・保存すること。"""
    mocks["db"].get_podcast_cache.return_value = _make_completed_cache()

    mocks["main"].main()

    mocks["db"].save_podcast.assert_called_once()


def test_cache_hit_completed_copies_audio_url_from_cache(mocks):
    """キャッシュヒット時、per-user Podcast の audio_url はキャッシュの共有 blob パスを使うこと。"""
    mocks["db"].get_podcast_cache.return_value = _make_completed_cache()

    mocks["main"].main()

    saved_podcast = mocks["db"].save_podcast.call_args[0][0]
    assert saved_podcast.audio_url == AUDIO_BLOB


def test_cache_hit_completed_copies_japanese_intro_from_cache(mocks):
    """キャッシュヒット時、per-user Podcast の japanese_intro_text はキャッシュから複写すること。"""
    mocks["db"].get_podcast_cache.return_value = _make_completed_cache()

    mocks["main"].main()

    saved_podcast = mocks["db"].save_podcast.call_args[0][0]
    assert saved_podcast.japanese_intro_text == "キャッシュイントロ"


def test_cache_hit_completed_copies_duration_from_cache(mocks):
    """キャッシュヒット時、per-user Podcast の duration_seconds はキャッシュから複写すること。"""
    mocks["db"].get_podcast_cache.return_value = _make_completed_cache()

    mocks["main"].main()

    saved_podcast = mocks["db"].save_podcast.call_args[0][0]
    assert saved_podcast.duration_seconds == 120


def test_cache_hit_completed_saves_podcast_with_correct_user_and_article(mocks):
    """キャッシュヒット時の per-user Podcast に正しい user_id / article_ids が設定されること。"""
    mocks["db"].get_podcast_cache.return_value = _make_completed_cache()

    mocks["main"].main()

    saved_podcast = mocks["db"].save_podcast.call_args[0][0]
    assert saved_podcast.user_id == "user1"
    assert ARTICLE_ID in saved_podcast.article_ids
    assert saved_podcast.status == "completed"
    assert saved_podcast.type == "single"


# ──────────────────────────────────────────────
# T4: キャッシュが processing → スキップ
# ──────────────────────────────────────────────


def test_cache_processing_skips_without_lock_acquire(mocks):
    """キャッシュが processing の場合、try_acquire_cache を呼ばずにスキップすること。

    spec §2.4 [3](b): 方式 B — 次回トリガーで補完する。
    """
    mocks["db"].get_podcast_cache.return_value = _make_processing_cache()

    mocks["main"].main()

    mocks["db"].try_acquire_cache.assert_not_called()
    mocks["db"].save_podcast.assert_not_called()


def test_cache_processing_does_not_call_generation(mocks):
    mocks["db"].get_podcast_cache.return_value = _make_processing_cache()

    mocks["main"].main()

    mocks["script_gen"].generate.assert_not_called()
    mocks["tts_gen"].generate_audio.assert_not_called()


# ──────────────────────────────────────────────
# T5: キャッシュミス (None) → フル生成
# ──────────────────────────────────────────────


def test_cache_miss_acquires_lock_and_generates(mocks):
    """キャッシュが None の場合、try_acquire_cache を呼んで生成フローを実行すること。"""
    # デフォルトで get_podcast_cache=None, try_acquire_cache=True

    mocks["main"].main()

    mocks["db"].try_acquire_cache.assert_called_once()
    mocks["script_gen"].generate.assert_called_once()
    mocks["tts_gen"].generate_audio.assert_called_once()


def test_cache_miss_uploads_to_cache_path(mocks):
    """キャッシュミス時、upload_cached_audio（共有パス）でアップロードすること。"""
    mocks["main"].main()

    mocks["storage"].upload_cached_audio.assert_called_once()


def test_generation_emits_duration_metric(mocks, caplog):
    """issue #83: 生成成功時に所要時間メトリクス（podcast_generation_duration）を emit する。"""
    import logging

    with caplog.at_level(logging.INFO):
        mocks["main"].main()

    metric_records = [
        r for r in caplog.records
        if getattr(r, "metric", None) == "podcast_generation_duration"
    ]
    assert metric_records, "duration メトリクスが emit されていない"
    rec = metric_records[0]
    assert rec.status == "completed"
    assert isinstance(rec.duration_ms, int)


def test_cache_miss_saves_cache_as_completed(mocks):
    """生成成功時、podcastCache を status=completed で保存すること。"""
    mocks["main"].main()

    # save_podcast_cache が呼ばれ、保存データが completed になっていること
    mocks["db"].save_podcast_cache.assert_called()
    saved_cache = mocks["db"].save_podcast_cache.call_args[0][0]
    assert saved_cache.status == "completed"


def test_cache_miss_saves_per_user_podcast(mocks):
    """生成成功時、per-user Podcast を保存すること。"""
    mocks["main"].main()

    mocks["db"].save_podcast.assert_called_once()
    saved_podcast = mocks["db"].save_podcast.call_args[0][0]
    assert saved_podcast.user_id == "user1"
    assert saved_podcast.status == "completed"


def test_cache_miss_per_user_podcast_uses_cache_audio_url(mocks):
    """生成フロー後の per-user Podcast audio_url は upload_cached_audio の戻り値（共有パス）であること。"""
    mocks["main"].main()

    saved_podcast = mocks["db"].save_podcast.call_args[0][0]
    assert saved_podcast.audio_url == AUDIO_BLOB


def test_cache_miss_saves_podcast_cache_before_per_user_podcast(mocks):
    """副作用の順序: save_podcast_cache(completed) → save_podcast の順であること。

    spec §2.4 注記: completed 書き込みが per-user Podcast より先 — 他ジョブの
    cache hit パスが completed を読めるようにするため。
    """
    call_order = []
    mocks["db"].save_podcast_cache.side_effect = lambda *a, **kw: call_order.append("cache")
    mocks["db"].save_podcast.side_effect = lambda *a, **kw: call_order.append("podcast")

    mocks["main"].main()

    assert call_order == ["cache", "podcast"], (
        f"期待: ['cache', 'podcast'], 実際: {call_order}"
    )


def test_cache_miss_duration_calculated_from_audio_bytes(mocks):
    """duration_seconds = len(audio_bytes) // _PCM_BYTES_PER_SECOND で計算されること。

    48_000 バイト / 48_000 = 1 秒。
    """
    from jobs.podcast_generator.tts_generator import TtsResult

    mocks["tts_gen"].generate_audio.return_value = TtsResult(
        audio=b"x" * 48_000,
        failed_segments=[],
        error_message=None,
    )

    mocks["main"].main()

    saved_podcast = mocks["db"].save_podcast.call_args[0][0]
    assert saved_podcast.duration_seconds == 1


def test_cache_miss_try_acquire_cache_receives_cache_key(mocks):
    """try_acquire_cache に正しい cache_key が渡されること。"""
    mocks["main"].main()

    acquire_args = mocks["db"].try_acquire_cache.call_args[0]
    assert acquire_args[0] == CACHE_KEY


# ──────────────────────────────────────────────
# T6: failed キャッシュ → 再確保
# ──────────────────────────────────────────────


def test_failed_cache_is_reacquired_for_retry(mocks):
    """status=failed のキャッシュは try_acquire_cache を通じて再確保されること（自己修復）。

    spec §2.4 [3](c): None または failed → try_acquire_cache を呼ぶ。
    """
    mocks["db"].get_podcast_cache.return_value = _make_failed_cache()
    mocks["db"].try_acquire_cache.return_value = True

    mocks["main"].main()

    mocks["db"].try_acquire_cache.assert_called_once()
    mocks["script_gen"].generate.assert_called_once()


# ──────────────────────────────────────────────
# T7: 確保レース敗北 → スキップ
# ──────────────────────────────────────────────


def test_acquire_race_loss_skips_generation(mocks):
    """try_acquire_cache=False（他ジョブが先行確保）の場合、生成せずにスキップすること。

    spec §2.4 [3](c): 確保レース敗北は方式 B でスキップ。次回トリガーで補完。
    """
    mocks["db"].get_podcast_cache.return_value = None
    mocks["db"].try_acquire_cache.return_value = False

    mocks["main"].main()

    mocks["script_gen"].generate.assert_not_called()
    mocks["db"].save_podcast.assert_not_called()


# ──────────────────────────────────────────────
# T8: 生成例外 → save_podcast_cache(failed)
# ──────────────────────────────────────────────


def test_generation_exception_saves_cache_as_failed(mocks):
    """Gemini/TTS が例外を送出した場合、podcastCache を status=failed で保存すること。

    spec §3.2 異常系: 自己修復のため failed を明示記録し、次回トリガーで再確保可能にする。
    """
    mocks["db"].get_podcast_cache.return_value = None
    mocks["db"].try_acquire_cache.return_value = True
    mocks["script_gen"].generate.side_effect = Exception("Gemini API error")

    mocks["main"].main()

    mocks["db"].save_podcast_cache.assert_called()
    saved_cache = mocks["db"].save_podcast_cache.call_args[0][0]
    assert saved_cache.status == "failed"


def test_generation_exception_does_not_save_per_user_podcast(mocks):
    """Gemini/TTS が例外を送出した場合、per-user Podcast は作成しないこと。

    spec §3.2: 生成失敗時は Podcast 0 件。
    """
    mocks["db"].get_podcast_cache.return_value = None
    mocks["db"].try_acquire_cache.return_value = True
    mocks["script_gen"].generate.side_effect = Exception("Gemini API error")

    mocks["main"].main()

    mocks["db"].save_podcast.assert_not_called()


def test_save_podcast_failure_does_not_revert_completed_cache(mocks):
    """per-user Podcast 保存失敗時、completed キャッシュが failed に巻き戻らないこと。

    生成フェーズ（Gemini/TTS/upload）の後に save_podcast が失敗しても、
    save_podcast_cache(completed) の状態は保持される。failed 書き込みは
    生成フェーズ失敗のみに限定される。

    リグレッション: 同一 try ブロックで completed → failed に上書きするバグへの防御。
    spec §3.2 意図: 「生成失敗」は failed に記録。per-user 永続化失敗は「生成失敗」ではない。
    """
    mocks["db"].save_podcast.side_effect = Exception("Firestore write error")

    with pytest.raises(Exception, match="Firestore write error"):
        mocks["main"].main()

    # save_podcast_cache は completed のみ 1 回呼ばれ、failed で上書きされないこと
    assert mocks["db"].save_podcast_cache.call_count == 1
    saved_cache = mocks["db"].save_podcast_cache.call_args[0][0]
    assert saved_cache.status == "completed"


def test_generation_exception_continues_to_next_article(mocks):
    """1 記事の生成失敗後、他記事の処理を継続すること（既存の except + continue 挙動を維持）。"""
    article2_id = "zzz999aaa111bbb222"
    from shared.models import Article, UserPrefs

    article2 = Article(
        id=article2_id,
        title="Second Article",
        url="https://example.com/second",
        source="hackernews",
        content="Second content",
        published_at=NOW,
        fetched_at=NOW,
    )

    mocks["db"].get_user_prefs.return_value = UserPrefs(
        user_id="user1",
        default_difficulty="toeic_900",
        starred_article_ids=[ARTICLE_ID, article2_id],
    )
    mocks["db"].get_recent_articles.return_value = [_make_article(), article2]

    # 1 記事目は失敗、2 記事目はキャッシュヒット
    from shared.models import PodcastCache

    cache2 = PodcastCache(
        cache_key=f"{article2_id}__toeic_900__ja-en",
        article_id=article2_id,
        difficulty="toeic_900",
        language="ja-en",
        status="completed",
        audio_url=f"podcasts/cache/{article2_id}__toeic_900__ja-en.mp3",
        japanese_intro_text="2件目イントロ",
        duration_seconds=90,
        created_at=NOW,
    )

    def _get_cache_side_effect(cache_key):
        if CACHE_KEY in cache_key:
            return None  # miss → try_acquire
        return cache2

    mocks["db"].get_podcast_cache.side_effect = _get_cache_side_effect
    mocks["db"].try_acquire_cache.return_value = True
    mocks["script_gen"].generate.side_effect = Exception("API error")

    mocks["main"].main()

    # 2 件目はキャッシュヒットで Podcast が保存されていること
    mocks["db"].save_podcast.assert_called_once()
    saved = mocks["db"].save_podcast.call_args[0][0]
    assert saved.article_ids == [article2_id]


# ──────────────────────────────────────────────
# T9: TTS 例外でも failed を記録
# ──────────────────────────────────────────────


def test_tts_exception_saves_cache_as_failed(mocks):
    """TTS 生成が例外を送出した場合も podcastCache を status=failed で保存すること。"""
    from jobs.podcast_generator.tts_generator import TtsGenerationError

    mocks["db"].get_podcast_cache.return_value = None
    mocks["db"].try_acquire_cache.return_value = True
    mocks["tts_gen"].generate_audio.side_effect = TtsGenerationError("TTS error")

    mocks["main"].main()

    mocks["db"].save_podcast_cache.assert_called()
    saved_cache = mocks["db"].save_podcast_cache.call_args[0][0]
    assert saved_cache.status == "failed"


# ──────────────────────────────────────────────
# T10: starred_article_ids が空
# ──────────────────────────────────────────────


def test_empty_starred_articles_generates_nothing(mocks):
    """starred_article_ids が空のユーザーは何も生成せず正常終了すること。

    spec §3.2: ループ 0 回。例外なく正常終了。
    """
    mocks["db"].get_user_prefs.return_value = _make_prefs(starred=[])

    mocks["main"].main()

    mocks["db"].get_podcast_cache.assert_not_called()
    mocks["db"].save_podcast.assert_not_called()
    mocks["script_gen"].generate.assert_not_called()


# ──────────────────────────────────────────────
# T11: cache_key の構成
# ──────────────────────────────────────────────


def test_cache_key_passed_to_get_podcast_cache(mocks):
    """get_podcast_cache に渡される cache_key が article_id__difficulty__language 形式であること。

    cache_key_for(article_id, difficulty, DEFAULT_PODCAST_LANGUAGE) と一致する。
    """
    from shared.utils import cache_key_for
    from shared.models import DEFAULT_PODCAST_LANGUAGE

    mocks["main"].main()

    expected_key = cache_key_for(ARTICLE_ID, "toeic_900", DEFAULT_PODCAST_LANGUAGE)
    mocks["db"].get_podcast_cache.assert_called_once_with(expected_key)


# ──────────────────────────────────────────────
# T12: 完成キャッシュ保存時に script_gen が受け取るスクリプトを per-user Podcast へ反映
# ──────────────────────────────────────────────


def test_cache_miss_per_user_podcast_uses_generated_intro(mocks):
    """キャッシュミス・生成成功時、per-user Podcast の japanese_intro_text は
    script_gen.generate() の返値（PodcastScript.japanese_intro）を使うこと。
    """
    from jobs.podcast_generator.script_generator import PodcastScript

    script = PodcastScript(title="", japanese_intro="生成されたイントロ", english_body="body")
    mocks["script_gen"].generate.return_value = script

    mocks["main"].main()

    saved_podcast = mocks["db"].save_podcast.call_args[0][0]
    assert saved_podcast.japanese_intro_text == "生成されたイントロ"


# ──────────────────────────────────────────────
# T9: difficulty フォールバック（env → prefs）
# ──────────────────────────────────────────────


def test_difficulty_falls_back_to_prefs_when_env_absent(mocks):
    """env DIFFICULTY が未指定・空のとき、prefs.default_difficulty へフォールバック。

    キャッシュキーや生成に prefs から取得した difficulty が使われることを検証する。
    """
    from unittest.mock import patch

    # env から DIFFICULTY を除去
    env = dict(_BASE_ENV)
    del env["DIFFICULTY"]

    with patch.dict("os.environ", env, clear=True):
        # prefs.default_difficulty="ielts_7" をセット
        mocks["db"].get_user_prefs.return_value = _make_prefs(
            starred=[ARTICLE_ID]
        )
        mocks["db"].get_user_prefs.return_value.default_difficulty = "ielts_7"

        mocks["main"].main()

        # cache_key_for が ielts_7 を使用した cache_key を生成
        # save_podcast_cache が呼ばれ、その difficulty 部分が ielts_7 であることを確認
        mocks["db"].save_podcast_cache.assert_called()
        saved_cache = mocks["db"].save_podcast_cache.call_args[0][0]
        assert saved_cache.difficulty == "ielts_7"
        assert "ielts_7" in saved_cache.cache_key


def test_difficulty_env_takes_precedence_over_prefs(mocks):
    """env DIFFICULTY が指定されている場合、prefs.default_difficulty より優先される（後方互換）。"""
    # env に DIFFICULTY="toeic_600" を指定
    env = dict(_BASE_ENV)
    env["DIFFICULTY"] = "toeic_600"

    with patch.dict("os.environ", env, clear=True):
        # prefs.default_difficulty="ielts_7" だが、env が優先される
        mocks["db"].get_user_prefs.return_value = _make_prefs(
            starred=[ARTICLE_ID]
        )
        mocks["db"].get_user_prefs.return_value.default_difficulty = "ielts_7"

        mocks["main"].main()

        # save_podcast_cache が呼ばれ、その difficulty 部分が toeic_600 であること
        saved_cache = mocks["db"].save_podcast_cache.call_args[0][0]
        assert saved_cache.difficulty == "toeic_600"
        assert "toeic_600" in saved_cache.cache_key


# ──────────────────────────────────────────────
# T13: dedup は completed-only（processing は非対象）
# ──────────────────────────────────────────────


def test_dedup_uses_completed_statuses_only(mocks):
    """podcast_exists_for_article が statuses=("completed",) で呼ばれることで、
    processing 行は dedup 対象外にされること。"""
    db = mocks["db"]

    mocks["main"].main()

    # 1 回目の呼び出しで statuses=("completed",) が指定されていることを確認
    db.podcast_exists_for_article.assert_called_with(
        "user1", ARTICLE_ID, "toeic_900", statuses=("completed",)
    )


# ──────────────────────────────────────────────
# T14: キャッシュヒット → 既存 processing 行を promote
# ──────────────────────────────────────────────


def test_cache_hit_promotes_existing_processing_row(mocks):
    """キャッシュヒット completed 時、既存の processing 行があれば promote_user_podcast で
    completed へ遷移させ、save_podcast は呼ばないこと。"""
    from shared.models import Podcast

    db = mocks["db"]

    # 既存の processing Podcast
    existing_pod = Podcast(
        id="pod-existing",
        type="single",
        article_ids=[ARTICLE_ID],
        difficulty="toeic_900",
        audio_url="",
        japanese_intro_text="",
        duration_seconds=0,
        status="processing",
        created_at=NOW,
        user_id="user1",
    )

    db.get_user_podcast_for_article.return_value = existing_pod
    db.get_podcast_cache.return_value = _make_completed_cache()

    mocks["main"].main()

    # promote_user_podcast が (pod-existing, "completed", ...) で呼ばれること
    db.promote_user_podcast.assert_called_once()
    promote_call = db.promote_user_podcast.call_args
    assert promote_call[0][0] == "pod-existing"  # podcast_id
    assert promote_call[0][1] == "completed"  # status
    assert promote_call[1]["audio_url"] == AUDIO_BLOB
    assert promote_call[1]["japanese_intro_text"] == "キャッシュイントロ"
    assert promote_call[1]["duration_seconds"] == 120

    # save_podcast は呼ばれないこと
    db.save_podcast.assert_not_called()


def test_cache_hit_no_existing_row_falls_back_to_save_podcast(mocks):
    """キャッシュヒット completed 時、既存行がなければ save_podcast で新規作成（後方互換）。"""
    db = mocks["db"]

    db.get_user_podcast_for_article.return_value = None  # 既存行なし
    db.get_podcast_cache.return_value = _make_completed_cache()

    mocks["main"].main()

    # promote_user_podcast は呼ばれないこと
    db.promote_user_podcast.assert_not_called()

    # save_podcast が呼ばれること
    db.save_podcast.assert_called_once()


# ──────────────────────────────────────────────
# T15: キャッシュミス・生成成功 → 既存行を promote
# ──────────────────────────────────────────────


def test_cache_miss_generation_success_promotes_to_completed(mocks):
    """キャッシュミス・生成成功時、既存の processing 行があれば promote_user_podcast で
    completed へ遷移させること。"""
    from shared.models import Podcast

    db = mocks["db"]

    existing_pod = Podcast(
        id="pod-existing",
        type="single",
        article_ids=[ARTICLE_ID],
        difficulty="toeic_900",
        audio_url="",
        japanese_intro_text="",
        duration_seconds=0,
        status="processing",
        created_at=NOW,
        user_id="user1",
    )

    db.get_user_podcast_for_article.return_value = existing_pod
    db.get_podcast_cache.return_value = None  # キャッシュミス
    db.try_acquire_cache.return_value = True

    mocks["main"].main()

    # promote_user_podcast が (pod-existing, "completed", ...) で呼ばれること
    db.promote_user_podcast.assert_called_once()
    promote_call = db.promote_user_podcast.call_args
    assert promote_call[0][0] == "pod-existing"
    assert promote_call[0][1] == "completed"
    assert promote_call[1]["audio_url"] == AUDIO_BLOB

    # save_podcast は呼ばれないこと（promote だけで完了）
    db.save_podcast.assert_not_called()


def test_cache_miss_no_existing_row_falls_back_to_save_podcast(mocks):
    """キャッシュミス・生成成功時、既存行がなければ save_podcast で新規作成（後方互換）。"""
    db = mocks["db"]

    db.get_user_podcast_for_article.return_value = None
    db.get_podcast_cache.return_value = None
    db.try_acquire_cache.return_value = True

    mocks["main"].main()

    # promote_user_podcast は呼ばれないこと
    db.promote_user_podcast.assert_not_called()

    # save_podcast が呼ばれること
    db.save_podcast.assert_called_once()


# ──────────────────────────────────────────────
# T16: 生成失敗 → 既存 processing 行を failed へ promote
# ──────────────────────────────────────────────


def test_generation_exception_promotes_existing_row_to_failed(mocks):
    """生成中に例外が発生し、既存の processing 行があれば、
    promote_user_podcast で failed へ遷移させること。"""
    from shared.models import Podcast

    db = mocks["db"]

    existing_pod = Podcast(
        id="pod-existing",
        type="single",
        article_ids=[ARTICLE_ID],
        difficulty="toeic_900",
        audio_url="",
        japanese_intro_text="",
        duration_seconds=0,
        status="processing",
        created_at=NOW,
        user_id="user1",
    )

    db.get_user_podcast_for_article.return_value = existing_pod
    db.get_podcast_cache.return_value = None
    db.try_acquire_cache.return_value = True
    mocks["script_gen"].generate.side_effect = Exception("Gemini API error")

    mocks["main"].main()

    # promote_user_podcast が (pod-existing, "failed", error_message=...) で呼ばれること
    db.promote_user_podcast.assert_called_once()
    promote_call = db.promote_user_podcast.call_args
    assert promote_call[0][0] == "pod-existing"
    assert promote_call[0][1] == "failed"
    assert "error_message" in promote_call[1]
    assert "Gemini API error" in promote_call[1]["error_message"]

    # save_podcast_cache (failed) も呼ばれること
    db.save_podcast_cache.assert_called_once()
    saved_cache = db.save_podcast_cache.call_args[0][0]
    assert saved_cache.status == "failed"


def test_generation_exception_no_existing_row_no_promote(mocks):
    """生成失敗時、既存 processing 行がなければ promote は呼ばない（既存動作を維持）。"""
    db = mocks["db"]

    db.get_user_podcast_for_article.return_value = None
    db.get_podcast_cache.return_value = None
    db.try_acquire_cache.return_value = True
    mocks["script_gen"].generate.side_effect = Exception("Gemini API error")

    mocks["main"].main()

    # promote_user_podcast は呼ばれないこと
    db.promote_user_podcast.assert_not_called()

    # save_podcast_cache (failed) は呼ばれること
    db.save_podcast_cache.assert_called_once()


# ──────────────────────────────────────────────
# T17: Notifier 統合テスト
# ──────────────────────────────────────────────


def test_generation_success_calls_notifier_notify_completion(mocks):
    """生成成功後、notifier.notify_completion が正しい引数で呼ばれることを検証する"""
    from unittest.mock import MagicMock

    db = mocks["db"]
    mock_notifier = MagicMock()

    # main() が notifier パラメータを受け取るようにパッチ
    with patch("jobs.podcast_generator.main.build_notifier") as mock_build:
        mock_build.return_value = mock_notifier

        db.get_user_podcast_for_article.return_value = None
        db.get_podcast_cache.return_value = None
        db.try_acquire_cache.return_value = True

        # notifier を依存として注入
        mocks["main"].main(notifier=mock_notifier)

        # notify_completion が呼ばれていることを確認
        mock_notifier.notify_completion.assert_called_once()
        call_args = mock_notifier.notify_completion.call_args

        # 引数を検証: user_id, title, body, data
        assert call_args[0][0] == "user1"  # user_id
        assert "生成完了" in call_args[1]["title"]
        assert "toeic_900" in call_args[1]["body"]
        assert "podcast_id" in call_args[1]["data"]
        assert "article_id" in call_args[1]["data"]


def test_generation_success_notifier_exception_does_not_break_job(mocks):
    """notifier.notify_completion が例外を出しても、ジョブは成功すること（非致命）"""
    from unittest.mock import MagicMock

    db = mocks["db"]
    mock_notifier = MagicMock()
    mock_notifier.notify_completion.side_effect = Exception("Notification failed")

    db.get_user_podcast_for_article.return_value = None
    db.get_podcast_cache.return_value = None
    db.try_acquire_cache.return_value = True

    # 例外を出さずに正常に完了する
    mocks["main"].main(notifier=mock_notifier)

    # save_podcast は呼ばれていること（ジョブは成功している）
    db.save_podcast.assert_called_once()


# ──────────────────────────────────────────────
# T12: partial_failed（一部失敗）
# ──────────────────────────────────────────────


def test_partial_failure_saves_partial_failed_podcast(mocks):
    """一部セグメント失敗時、Podcast の status が partial_failed であること。"""
    from jobs.podcast_generator.tts_generator import TtsResult

    partial_result = TtsResult(
        audio=b"x" * 48_000,  # 成功分音声（日本語のみ）
        failed_segments=["english_body"],
        error_message="TTS failed for segments: english_body",
    )
    mocks["tts_gen"].generate_audio.return_value = partial_result

    mocks["main"].main()

    saved_podcast = mocks["db"].save_podcast.call_args[0][0]
    assert saved_podcast.status == "partial_failed"
    assert "english_body" in saved_podcast.error_message


def test_partial_failure_uploads_partial_audio(mocks):
    """一部失敗でも upload_cached_audio が呼ばれること。"""
    from jobs.podcast_generator.tts_generator import TtsResult

    partial_result = TtsResult(
        audio=b"x" * 48_000,
        failed_segments=["english_body"],
        error_message="TTS failed for segments: english_body",
    )
    mocks["tts_gen"].generate_audio.return_value = partial_result

    mocks["main"].main()

    mocks["storage"].upload_cached_audio.assert_called_once()


def test_partial_failure_saves_failed_cache(mocks):
    """一部失敗時、キャッシュは status=failed で保存されること。"""
    from jobs.podcast_generator.tts_generator import TtsResult

    partial_result = TtsResult(
        audio=b"x" * 48_000,
        failed_segments=["english_body"],
        error_message="TTS failed for segments: english_body",
    )
    mocks["tts_gen"].generate_audio.return_value = partial_result

    mocks["main"].main()

    saved_cache = mocks["db"].save_podcast_cache.call_args[0][0]
    assert saved_cache.status == "failed"


def test_partial_failure_with_existing_processing_promotes_to_partial_failed(mocks):
    """一部失敗時に既存 processing 行があれば、promote_user_podcast で partial_failed へ遷移。"""
    from jobs.podcast_generator.tts_generator import TtsResult
    from shared.models import Podcast

    partial_result = TtsResult(
        audio=b"x" * 48_000,
        failed_segments=["english_body"],
        error_message="TTS failed for segments: english_body",
    )
    mocks["tts_gen"].generate_audio.return_value = partial_result

    # 既存 processing 行を返す
    existing_podcast = Podcast(
        id="existing-processing-id",
        type="single",
        article_ids=[ARTICLE_ID],
        difficulty="toeic_900",
        audio_url="",
        japanese_intro_text="",
        duration_seconds=0,
        status="processing",
        created_at=NOW,
        user_id="user1",
    )
    mocks["db"].get_user_podcast_for_article.return_value = existing_podcast

    mocks["main"].main()

    # promote_user_podcast が partial_failed で呼ばれること
    mocks["db"].promote_user_podcast.assert_called_once()
    call_args = mocks["db"].promote_user_podcast.call_args
    assert call_args[0][0] == "existing-processing-id"
    assert call_args[0][1] == "partial_failed"
    assert call_args[1]["error_message"] == "TTS failed for segments: english_body"


def test_partial_failure_sends_notification(mocks):
    """一部失敗でも notifier.notify_completion が呼ばれること。"""
    from jobs.podcast_generator.tts_generator import TtsResult
    from unittest.mock import MagicMock

    partial_result = TtsResult(
        audio=b"x" * 48_000,
        failed_segments=["english_body"],
        error_message="TTS failed for segments: english_body",
    )
    mocks["tts_gen"].generate_audio.return_value = partial_result

    mock_notifier = MagicMock()
    mocks["main"].main(notifier=mock_notifier)

    mock_notifier.notify_completion.assert_called_once()


def test_complete_success_saves_completed_podcast(mocks):
    """全セグメント成功時、status が completed のまま保たれること（非回帰）。"""
    from jobs.podcast_generator.tts_generator import TtsResult

    complete_result = TtsResult(
        audio=b"x" * 48_000,
        failed_segments=[],
        error_message=None,
    )
    mocks["tts_gen"].generate_audio.return_value = complete_result

    mocks["main"].main()

    saved_podcast = mocks["db"].save_podcast.call_args[0][0]
    assert saved_podcast.status == "completed"
    assert saved_podcast.error_message is None
