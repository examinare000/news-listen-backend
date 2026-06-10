"""Cloud Run Job エントリポイント: レコメンデーション計算。"""
import logging
import os
from datetime import datetime, timezone

from shared.firestore_client import FirestoreClient
from shared.gemini_client import GeminiClient
from shared.models import Recommendation
from jobs.recommendation.recommender import Recommender

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    # KeyError で即座に失敗させる。os.environ.get("USER_ID", "default") のような
    # サイレントフォールバックは複数ユーザーのデータ混在バグを引き起こす。
    user_id = os.environ["USER_ID"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    db = FirestoreClient()
    gemini = GeminiClient()
    recommender = Recommender(gemini_client=gemini)

    prefs = db.get_user_prefs(user_id)
    articles = db.get_recent_articles(limit=200)

    exclude_ids = set(prefs.dismissed_article_ids)
    candidates = [a for a in articles if a.id not in exclude_ids]

    starred_articles = [a for a in articles if a.id in set(prefs.starred_article_ids)]
    dismissed_articles = [a for a in articles if a.id in set(prefs.dismissed_article_ids)]

    logger.info("Scoring %d candidates for user %s", len(candidates), user_id)
    scores = recommender.score_articles(
        candidates,
        starred_articles=starred_articles,
        dismissed_articles=dismissed_articles,
    )
    scores.sort(key=lambda s: s.score, reverse=True)

    rec = Recommendation(
        user_id=user_id,
        date=today,
        articles=scores,
        generated_at=datetime.now(timezone.utc),
    )
    db.save_recommendation(rec)
    logger.info("Saved recommendation with %d articles", len(scores))


if __name__ == "__main__":
    main()
