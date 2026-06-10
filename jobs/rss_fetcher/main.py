"""Cloud Run Job エントリポイント: RSS フェッチ。"""
import logging
import os
from datetime import datetime, timezone

from shared.firestore_client import FirestoreClient
from jobs.rss_fetcher.rss_fetcher import RssFetcher
from jobs.rss_fetcher.content_extractor import ContentExtractor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# RSS 本文がこの文字数未満の場合は trafilatura でスクレイピングを試みる。
# 大半の RSS フィードはサマリーのみ提供し、本文は数百文字以上あるため 200 を閾値とした。
_MIN_CONTENT_LENGTH = 200


def main() -> None:
    # KeyError で即座に失敗させる。os.environ.get("USER_ID", "default") のような
    # サイレントフォールバックは複数ユーザーのデータ混在バグを引き起こす。
    user_id = os.environ["USER_ID"]
    db = FirestoreClient()
    fetcher = RssFetcher()
    extractor = ContentExtractor()

    prefs = db.get_user_prefs(user_id)
    logger.info("Processing %d RSS sources for user %s", len(prefs.rss_sources), user_id)

    new_count = 0
    for source in prefs.rss_sources:
        logger.info("Fetching %s", source.url)
        try:
            articles = fetcher.fetch(source.url, source_name=source.name)
        except Exception as e:
            logger.error("Failed to fetch %s: %s", source.url, e)
            continue

        for article in articles:
            if db.article_exists(article.id):
                continue
            # 本文が RSS に含まれていない場合は Web スクレイピングで取得
            if len(article.content) < _MIN_CONTENT_LENGTH:
                extracted = extractor.extract(article.url)
                if extracted:
                    # content_fetched_at を同期する（モデルのフィールド設計意図を守る）
                    article.content = extracted
                    article.content_fetched_at = datetime.now(timezone.utc)
            db.save_article(article)
            new_count += 1

    logger.info("Saved %d new articles", new_count)


if __name__ == "__main__":
    main()
