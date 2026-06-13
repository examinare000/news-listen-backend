"""Cloud Run Job エントリポイント: Podcast 生成。"""
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import get_args

from shared.firestore_client import FirestoreClient
from shared.gemini_client import GeminiClient
from shared.models import DEFAULT_PODCAST_LANGUAGE, DifficultyLevel, Podcast, PodcastCache
from shared.storage_client import StorageClient
from shared.utils import cache_key_for
from jobs.podcast_generator.script_generator import ScriptGenerator
from jobs.podcast_generator.tts_generator import TtsGenerator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Gemini TTS は 24kHz モノラル PCM を返す（16bit signed = 2 bytes/sample）。
# bytes / (24000 Hz × 2 bytes) = 秒数
_PCM_BYTES_PER_SECOND = 48_000

# 関連記事の最大取得件数
_MAX_RELATED_ARTICLES = 5

# 許可される難易度値（models.DifficultyLevel Literal から自動生成）
_VALID_DIFFICULTIES: tuple[str, ...] = get_args(DifficultyLevel)


def _build_user_podcast(
    user_id: str,
    article_id: str,
    difficulty: str,
    audio_url: str,
    japanese_intro_text: str,
    duration_seconds: int,
) -> Podcast:
    """per-user Podcast を構築する。

    キャッシュヒット時・ミス生成後の両方で共通利用するヘルパー。
    audio_url は常に共有 blob パスを渡す（配信・署名付き URL 生成はそのまま動く）。
    """
    return Podcast(
        id=str(uuid.uuid4()),
        type="single",
        article_ids=[article_id],
        difficulty=difficulty,
        audio_url=audio_url,
        japanese_intro_text=japanese_intro_text,
        duration_seconds=duration_seconds,
        status="completed",
        created_at=datetime.now(timezone.utc),
        user_id=user_id,
    )


def main() -> None:
    # KeyError で即座に失敗させる。os.environ.get("USER_ID", "default") のような
    # サイレントフォールバックは複数ユーザーのデータ混在バグを引き起こす。
    user_id = os.environ["USER_ID"]
    difficulty = os.environ.get("DIFFICULTY", "toeic_900")

    # 無効な difficulty はループ内の except Exception で吸収され全件生成失敗しても
    # 正常終了扱いになるため、起動時に検証して早期失敗させる。
    if difficulty not in _VALID_DIFFICULTIES:
        raise SystemExit(
            f"Invalid DIFFICULTY={difficulty!r}. Valid values: {_VALID_DIFFICULTIES}"
        )
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    db = FirestoreClient()
    gemini = GeminiClient()
    storage = StorageClient()
    script_gen = ScriptGenerator(gemini_client=gemini)
    tts_gen = TtsGenerator(gemini_client=gemini)

    prefs = db.get_user_prefs(user_id)
    all_articles = db.get_recent_articles(limit=500)
    articles_by_id = {a.id: a for a in all_articles}

    for article_id in prefs.starred_article_ids:
        # (a) per-user 冪等チェック: ユーザーが既に当該 Podcast を保有していればスキップ。
        # キャッシュ参照より前段に置き、不要な Firestore ラウンドトリップを防ぐ。
        if db.podcast_exists_for_article(user_id, article_id, difficulty):
            logger.info("Podcast already exists for article %s, skipping", article_id)
            continue

        article = articles_by_id.get(article_id)
        if not article:
            logger.warning("Article %s not found in Firestore", article_id)
            continue

        ck = cache_key_for(article_id, difficulty, DEFAULT_PODCAST_LANGUAGE)
        cache = db.get_podcast_cache(ck)

        # (b) キャッシュヒット (completed): Gemini/TTS を呼ばず共有成果物を参照する per-user Podcast を作成。
        if cache is not None and cache.status == "completed":
            podcast = _build_user_podcast(
                user_id,
                article_id,
                difficulty,
                cache.audio_url,
                cache.japanese_intro_text,
                cache.duration_seconds,
            )
            db.save_podcast(podcast)
            logger.info("Cache hit: saved podcast for article %s from shared cache", article_id)
            continue

        # (d) キャッシュ生成中 (processing): 今回はスキップ（方式 B）。次回トリガーで補完。
        if cache is not None and cache.status == "processing":
            logger.info("Cache processing for article %s, skipping (method B)", article_id)
            continue

        # (c) キャッシュミス (None / failed): トランザクションで processing を原子的に確保。
        acquired = db.try_acquire_cache(ck, article_id, difficulty, DEFAULT_PODCAST_LANGUAGE)
        if not acquired:
            # 確保レース敗北: 他ジョブが先行確保 → スキップ（方式 B）
            logger.info("Failed to acquire cache for %s, skipping", article_id)
            continue

        # 関連記事: 同じソースの最新件数分（簡易実装）
        related = [
            a for a in all_articles
            if a.source == article.source and a.id != article.id
        ][:_MAX_RELATED_ARTICLES]

        logger.info("Generating podcast for: %s", article.title)
        try:
            script = script_gen.generate(article, related, difficulty, today)
            audio_bytes = tts_gen.generate_audio(script)
            audio_blob_path = storage.upload_cached_audio(ck, audio_bytes)
            duration = len(audio_bytes) // _PCM_BYTES_PER_SECOND
        except Exception as e:
            logger.error("Failed to generate podcast for %s: %s", article_id, e)
            # 自己修復: 生成失敗（Gemini/TTS/upload）のみ failed を記録し、
            # 次回トリガーで再確保・再生成できるようにする。
            # 永続化フェーズの失敗は completed を上書きしない。
            failed_cache = PodcastCache(
                cache_key=ck,
                article_id=article_id,
                difficulty=difficulty,
                language=DEFAULT_PODCAST_LANGUAGE,
                status="failed",
                created_at=datetime.now(timezone.utc),
            )
            db.save_podcast_cache(failed_cache)
            continue

        # 生成成功 → 永続化（生成フェーズの failed リカバリ対象外）
        # completed を先に書く — 後発ジョブが cache hit パスで読めるようにするため。
        # save_podcast 失敗時も completed は保持され、次回の cache hit (b) 経路で補完される。
        completed_cache = PodcastCache(
            cache_key=ck,
            article_id=article_id,
            difficulty=difficulty,
            language=DEFAULT_PODCAST_LANGUAGE,
            status="completed",
            audio_url=audio_blob_path,
            japanese_intro_text=script.japanese_intro,
            duration_seconds=duration,
            created_at=datetime.now(timezone.utc),
        )
        db.save_podcast_cache(completed_cache)

        podcast = _build_user_podcast(
            user_id,
            article_id,
            difficulty,
            audio_blob_path,
            script.japanese_intro,
            duration,
        )
        db.save_podcast(podcast)
        logger.info("Saved podcast %s for article %s", podcast.id, article_id)


if __name__ == "__main__":
    main()
