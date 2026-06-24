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

        mock_script = PodcastScript(japanese_intro="生成イントロ", english_body="English body.")
        script_gen.generate.return_value = mock_script
        # _PCM_BYTES_PER_SECOND = 48_000 → 1 秒分の音声データ
        tts_gen.generate_audio.return_value = b"x" * 48_000
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
    mocks["tts_gen"].generate_audio.return_value = b"x" * 48_000

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
    mocks["db"].get_podcast_cache.return_value = None
    mocks["db"].try_acquire_cache.return_value = True
    mocks["tts_gen"].generate_audio.side_effect = Exception("TTS error")

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

    script = PodcastScript(japanese_intro="生成されたイントロ", english_body="body")
    mocks["script_gen"].generate.return_value = script

    mocks["main"].main()

    saved_podcast = mocks["db"].save_podcast.call_args[0][0]
    assert saved_podcast.japanese_intro_text == "生成されたイントロ"
