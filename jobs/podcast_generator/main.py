"""Cloud Run Job エントリポイント: Podcast 生成。"""
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import get_args

from shared.firestore_client import FirestoreClient
from shared.gemini_client import GeminiClient
from shared.models import DifficultyLevel, Podcast
from shared.storage_client import StorageClient
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
        if db.podcast_exists_for_article(user_id, article_id, difficulty):
            logger.info("Podcast already exists for article %s, skipping", article_id)
            continue

        article = articles_by_id.get(article_id)
        if not article:
            logger.warning("Article %s not found in Firestore", article_id)
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

            podcast_id = str(uuid.uuid4())
            # upload_audio は GCS blob パスを返す（公開 URL ではない）
            # 再生時は StorageClient.generate_audio_url() で署名付き URL を生成すること
            audio_blob_path = storage.upload_audio(podcast_id, difficulty, audio_bytes)

            duration = len(audio_bytes) // _PCM_BYTES_PER_SECOND

            podcast = Podcast(
                id=podcast_id,
                type="single",
                article_ids=[article_id],
                difficulty=difficulty,
                audio_url=audio_blob_path,  # GCS パスを保存
                japanese_intro_text=script.japanese_intro,
                duration_seconds=duration,
                status="completed",
                created_at=datetime.now(timezone.utc),
                user_id=user_id,
            )
            db.save_podcast(podcast)
            logger.info("Saved podcast %s for article %s", podcast_id, article_id)
        except Exception as e:
            logger.error("Failed to generate podcast for %s: %s", article_id, e)
            continue


if __name__ == "__main__":
    main()
