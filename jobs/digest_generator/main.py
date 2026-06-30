"""Cloud Run Job エントリポイント: 日次ダイジェスト生成。"""
import logging
import os
from datetime import datetime, timezone

from shared.firestore_client import FirestoreClient
from shared.models import Article, Podcast, Recommendation
from shared.storage_client import StorageClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# PCM 24kHz・16bit・モノラル = 48000 bytes/秒（podcast_generator と同値）
_PCM_BYTES_PER_SECOND = 48_000


def _build_digest_podcast(
    user_id: str,
    digest_id: str,
    article_ids: list[str],
    audio_url: str,
    japanese_intro_text: str,
    duration_seconds: int,
    difficulty: str = "toeic_900",
    status: str = "completed",
    error_message: str | None = None,
    title: str = "",
) -> Podcast:
    """digest 専用 Podcast を構築する。

    Args:
        user_id: ユーザーID
        digest_id: digest の決定論的 ID（user_id_YYYY-MM-DD_digest）
        article_ids: 含める記事 ID リスト
        audio_url: 音声 blob パス
        japanese_intro_text: 日本語イントロ
        duration_seconds: 音声長（秒）
        status: Podcast status（既定 "completed"）
        error_message: エラー時の説明
        title: 台本タイトル（1センテンス日本語要約）

    Returns:
        Podcast（type="digest"）
    """
    return Podcast(
        id=digest_id,
        type="digest",
        article_ids=article_ids,
        difficulty=difficulty,  # 生成時の難易度（prefs.default_difficulty）を保存
        audio_url=audio_url,
        japanese_intro_text=japanese_intro_text,
        duration_seconds=duration_seconds,
        status=status,  # type: ignore[arg-type]
        error_message=error_message,
        title=title,
        created_at=datetime.now(timezone.utc),
        user_id=user_id,
    )


def select_digest_articles(
    starred_ids: set[str],
    recommendation: Recommendation | None,
    articles_by_id: dict[str, Article],
    count: int,
) -> list[Article]:
    """starred ∩ recommendation.articles を score 降順で選定する純粋関数。

    Args:
        starred_ids: ユーザーの star 記事 ID 集合
        recommendation: 当日の recommendation（None なら空）
        articles_by_id: article_id → Article マッピング
        count: リクエスト記事数（内部で 3〜10 にクランプ）

    Returns:
        選定記事リスト（0 件の場合は空リスト）
    """
    if recommendation is None or not recommendation.articles:
        return []

    # count を 3〜10 にクランプ
    clamped_count = max(3, min(10, count))

    # recommendation.articles を score 降順＆安定ソート
    scored = [
        (article.article_id, article.score)
        for article in recommendation.articles
    ]
    scored_sorted = sorted(scored, key=lambda x: -x[1])

    # starred ∩ recommendation.articles から上位 N 件を選定
    selected = []
    for article_id, _ in scored_sorted:
        if article_id in starred_ids and article_id in articles_by_id:
            selected.append(articles_by_id[article_id])
            if len(selected) >= clamped_count:
                break

    return selected


def main(notifier=None, *, db=None, storage=None, script_gen=None, tts_gen=None) -> None:
    """Cloud Run Job エントリポイント: 日次ダイジェスト生成。

    Args:
        notifier: 通知送信機（テスト時に差し替え可能）
        db: FirestoreClient（未指定なら構築）
        storage: StorageClient（未指定なら構築）
        script_gen: ScriptGenerator（未指定なら構築）
        tts_gen: TtsGenerator（未指定なら構築）
    """
    from shared.gemini_client import GeminiClient
    from shared.notifier import build_notifier
    from jobs.podcast_generator.script_generator import ScriptGenerator
    from jobs.podcast_generator.tts_generator import TtsGenerator, TtsGenerationError

    user_id = os.environ["USER_ID"]

    if db is None:
        db = FirestoreClient()
    if storage is None:
        storage = StorageClient()
    if script_gen is None:
        gemini = GeminiClient()
        script_gen = ScriptGenerator(gemini_client=gemini)
    if tts_gen is None:
        gemini = GeminiClient()
        tts_gen = TtsGenerator(gemini_client=gemini)

    prefs = db.get_user_prefs(user_id)

    # digest_enabled が False なら何もせず終了
    if not prefs.digest_enabled:
        logger.info(
            f"Digest disabled for user {user_id}. Skipping.",
            extra={"user_id": user_id},
        )
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 冪等チェック：既存 digest があればスキップ
    digest_id = f"{user_id}_{today}_digest"
    existing_digest = db.get_podcast(digest_id)
    if existing_digest is not None:
        logger.info(
            f"Digest already generated for {user_id} on {today}. Skipping.",
            extra={"user_id": user_id, "digest_id": digest_id},
        )
        return

    # 記事選定
    starred_ids = set(prefs.starred_article_ids)
    all_articles = db.get_recent_articles(limit=500)
    articles_by_id = {a.id: a for a in all_articles}

    rec = db.get_recommendation(user_id, today)
    digest_articles = select_digest_articles(
        starred_ids=starred_ids,
        recommendation=rec,
        articles_by_id=articles_by_id,
        count=prefs.digest_article_count,
    )

    # 0 件なら生成せず終了
    if not digest_articles:
        logger.info(
            "No articles selected for digest. Skipping.",
            extra={"user_id": user_id},
        )
        return

    logger.info(
        f"Digest generation for {user_id}: {len(digest_articles)} articles selected",
        extra={"user_id": user_id, "article_count": len(digest_articles)},
    )

    # スクリプト生成
    difficulty = prefs.default_difficulty
    script = script_gen.generate_digest(
        articles=digest_articles,
        difficulty=difficulty,
        date_str=today,
    )

    # TTS 生成
    try:
        result = tts_gen.generate_audio(script)
    except TtsGenerationError as e:
        logger.error(
            f"TTS generation failed for digest {digest_id}",
            extra={"user_id": user_id, "digest_id": digest_id, "error": str(e)},
        )
        return

    # 音声アップロード。digest はユーザー固有（starred 集合依存）でクロスユーザー
    # キャッシュを使わないため、決定論的 digest_id をそのまま blob キーにする。
    audio_key = digest_id
    blob_path = storage.upload_cached_audio(audio_key, result.audio)

    # Podcast ビルダー
    status = "partial_failed" if result.failed_segments else "completed"
    podcast = _build_digest_podcast(
        user_id=user_id,
        digest_id=digest_id,
        article_ids=[a.id for a in digest_articles],
        audio_url=blob_path,
        japanese_intro_text=script.japanese_intro,
        duration_seconds=len(result.audio) // _PCM_BYTES_PER_SECOND,
        difficulty=difficulty,
        status=status,
        error_message=result.error_message if result.failed_segments else None,
        title=script.title,
    )

    # Podcast 保存
    db.save_podcast(podcast)

    logger.info(
        f"Digest podcast created: {digest_id}",
        extra={"user_id": user_id, "digest_id": digest_id},
    )

    # 通知送信（失敗はジョブ成功に影響しない）
    if notifier is None:
        notifier = build_notifier(db, os.environ)

    try:
        notifier.notify_completion(
            user_id=user_id,
            title="日次ダイジェスト生成完了",
            body=f"{len(digest_articles)}件の記事をまとめたダイジェストが生成されました",
            data={"podcast_id": digest_id, "url": "/feed"},
        )
    except Exception as e:
        logger.warning(
            f"Notification send failed for {digest_id}",
            extra={"user_id": user_id, "error": str(e)},
        )


if __name__ == "__main__":
    main()
