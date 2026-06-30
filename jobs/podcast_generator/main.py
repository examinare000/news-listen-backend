"""Cloud Run Job エントリポイント: Podcast 生成。"""
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import get_args

from shared.firestore_client import FirestoreClient
from shared.gemini_client import GeminiClient
from shared.logging_config import configure_logging, emit_metric
from shared.models import DEFAULT_PODCAST_LANGUAGE, DifficultyLevel, Podcast, PodcastCache
from shared.notifier import build_notifier
from shared.storage_client import StorageClient
from shared.utils import cache_key_for
from jobs.podcast_generator.script_generator import ScriptGenerator
from jobs.podcast_generator.tts_generator import TtsGenerator

# 構造化ログ＋機微情報スクラブを設定（issue #83）。冪等。
configure_logging()
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
    status: str = "completed",
    error_message: str | None = None,
) -> Podcast:
    """per-user Podcast を構築する。

    キャッシュヒット時・ミス生成後の両方で共通利用するヘルパー。
    audio_url は常に共有 blob パスを渡す（配信・署名付き URL 生成はそのまま動く）。

    Args:
        status: Podcast status（既定 "completed"）。partial_failed 等にも対応。
        error_message: 一部失敗時のエラー文言（既定 None、status != "completed" 時に設定）。
    """
    return Podcast(
        id=str(uuid.uuid4()),
        type="single",
        article_ids=[article_id],
        difficulty=difficulty,
        audio_url=audio_url,
        japanese_intro_text=japanese_intro_text,
        duration_seconds=duration_seconds,
        status=status,
        error_message=error_message,
        created_at=datetime.now(timezone.utc),
        user_id=user_id,
    )


def _persist_user_podcast(
    db: FirestoreClient,
    user_id: str,
    article_id: str,
    difficulty: str,
    audio_url: str,
    japanese_intro_text: str,
    duration_seconds: int,
    status: str = "completed",
    error_message: str | None = None,
) -> str:
    """star 経由で作られた processing 行があれば promote、無ければ新規 save（後方互換）。

    WHY: star 起点の processing 行を重複させず同一 id で completed へ昇格。
    star 非経由トリガーは従来どおり新規保存（後方互換）。
    永続化した Podcast の id を返す（ログでの追跡用）。

    Args:
        status: Podcast status（既定 "completed"）。partial_failed 等にも対応。
        error_message: 一部失敗時のエラー文言（既定 None）。
    """
    existing = db.get_user_podcast_for_article(user_id, article_id, difficulty)
    if existing is not None:
        db.promote_user_podcast(
            existing.id,
            status,
            audio_url=audio_url,
            japanese_intro_text=japanese_intro_text,
            duration_seconds=duration_seconds,
            error_message=error_message,
        )
        return existing.id
    podcast = _build_user_podcast(
        user_id, article_id, difficulty,
        audio_url, japanese_intro_text, duration_seconds,
        status=status, error_message=error_message,
    )
    db.save_podcast(podcast)
    return podcast.id


def main(notifier=None) -> None:
    """Cloud Run Job エントリポイント: Podcast 生成。

    Args:
        notifier: 通知送信機（テスト時に差し替え可能）。未指定なら環境変数から構築。
    """
    # KeyError で即座に失敗させる。os.environ.get("USER_ID", "default") のような
    # サイレントフォールバックは複数ユーザーのデータ混在バグを引き起こす。
    user_id = os.environ["USER_ID"]

    # env DIFFICULTY が指定されていれば先に検証（バリデーション早期失敗）。
    # 指定されていない場合は prefs から取得した後に検証する。
    env_difficulty = os.environ.get("DIFFICULTY")
    if env_difficulty and env_difficulty not in _VALID_DIFFICULTIES:
        raise SystemExit(
            f"Invalid DIFFICULTY={env_difficulty!r}. Valid values: {_VALID_DIFFICULTIES}"
        )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    db = FirestoreClient()
    gemini = GeminiClient()
    storage = StorageClient()
    script_gen = ScriptGenerator(gemini_client=gemini)
    tts_gen = TtsGenerator(gemini_client=gemini)

    # notifier 未指定なら環境から構築（鍵未設定なら no-op）
    if notifier is None:
        notifier = build_notifier(db, os.environ)

    prefs = db.get_user_prefs(user_id)

    # env DIFFICULTY が指定されていれば使用。未指定・空の場合のみ prefs へフォールバック。
    # WHY: env 未指定時のみ prefs.default_difficulty を採用し、既存経路（env 指定）は
    # 後方互換を保つ。JobTrigger に env 組み立て責務を持たせない（境界の漏れ防止）。
    difficulty = env_difficulty or prefs.default_difficulty

    # prefs からのフォールバック時のバリデーション（env 指定時は上記で実施済み）。
    if not env_difficulty and difficulty not in _VALID_DIFFICULTIES:
        raise SystemExit(
            f"Invalid DIFFICULTY={difficulty!r}. Valid values: {_VALID_DIFFICULTIES}"
        )
    all_articles = db.get_recent_articles(limit=500)
    articles_by_id = {a.id: a for a in all_articles}

    for article_id in prefs.starred_article_ids:
        # (a) per-user 冪等チェック: ユーザーが既に当該 Podcast を保有していればスキップ。
        # processing 行は dedup 対象外（star が作った processing 行があっても生成を続行し promote する）。
        # キャッシュ参照より前段に置き、不要な Firestore ラウンドトリップを防ぐ。
        if db.podcast_exists_for_article(user_id, article_id, difficulty, statuses=("completed",)):
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
            _persist_user_podcast(
                db,
                user_id,
                article_id,
                difficulty,
                cache.audio_url,
                cache.japanese_intro_text,
                cache.duration_seconds,
            )
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
        _gen_started = time.perf_counter()
        try:
            script = script_gen.generate(article, related, difficulty, today)
            result = tts_gen.generate_audio(script)
            audio_bytes = result.audio
            audio_blob_path = storage.upload_cached_audio(ck, audio_bytes)
            duration = len(audio_bytes) // _PCM_BYTES_PER_SECOND
            # 生成所要時間の SLO 信号（2 分以内・PRD §2/§6 / issue #83）。Cloud Logging の
            # log-based metric で集計し、しきい値超過をアラートする。
            emit_metric(
                logger,
                "podcast_generation_duration",
                status="completed",
                duration_ms=int((time.perf_counter() - _gen_started) * 1000),
                article_id=article_id,
            )
        except Exception as e:
            emit_metric(
                logger,
                "podcast_generation_duration",
                status="failed",
                duration_ms=int((time.perf_counter() - _gen_started) * 1000),
                article_id=article_id,
            )
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
            # 生成失敗時は per-user processing 行を failed へ遷移させ stuck を防ぐ。
            # promote は processing のときだけ書くので後発 completed を踏み潰さない。
            existing = db.get_user_podcast_for_article(user_id, article_id, difficulty)
            if existing is not None:
                db.promote_user_podcast(existing.id, "failed", error_message=str(e))
            continue

        # TtsResult: failed_segments の有無で全成功／部分失敗を分岐する。
        # 部分失敗でも成功分の音声は完成しているため、配信・通知は完了扱いで行う。
        is_partial = bool(result.failed_segments)

        # キャッシュ: 全成功は成果物を completed で共有キャッシュへ載せる。部分失敗は
        # 欠落音声でクロスユーザーキャッシュを汚さないよう failed に留め、次回トリガーで
        # 自己修復（再確保・再生成）させる。failed キャッシュは except 経路と同形で
        # audio_url を持たせない（共有配布されるのは completed のみ）。
        if is_partial:
            cache = PodcastCache(
                cache_key=ck,
                article_id=article_id,
                difficulty=difficulty,
                language=DEFAULT_PODCAST_LANGUAGE,
                status="failed",
                created_at=datetime.now(timezone.utc),
            )
        else:
            cache = PodcastCache(
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
        db.save_podcast_cache(cache)

        # per-user Podcast: 部分失敗は partial_failed + error_message、全成功は completed。
        # 成功分の音声 blob は両者とも参照する（partial でも再生可能）。
        podcast_id = _persist_user_podcast(
            db,
            user_id,
            article_id,
            difficulty,
            audio_blob_path,
            script.japanese_intro,
            duration,
            status="partial_failed" if is_partial else "completed",
            error_message=result.error_message,
        )
        logger.info(
            "Saved %spodcast %s for article %s",
            "partial " if is_partial else "", podcast_id, article_id,
        )

        # 生成完了通知（送信失敗はジョブ成功に影響しない）。部分失敗時は文面で明示する。
        if is_partial:
            notify_title = "Podcast 生成完了（一部失敗）"
            notify_body = f"記事のポッドキャストが生成されました（{difficulty}、一部セグメント失敗）"
        else:
            notify_title = "Podcast 生成完了"
            notify_body = f"記事のポッドキャストが生成されました（{difficulty}）"
        try:
            notifier.notify_completion(
                user_id,
                title=notify_title,
                body=notify_body,
                # url は Service Worker の notificationclick が遷移先に使う（未指定だと "/" 固定）。
                data={"podcast_id": podcast_id, "article_id": article_id, "url": "/feed"},
            )
        except Exception:
            logger.warning("Failed to send push notification for podcast %s", podcast_id)


if __name__ == "__main__":
    main()
