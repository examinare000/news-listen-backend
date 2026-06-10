"""RSS フィード取得と記事 ID 生成。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser

from shared.models import Article
from shared.utils import article_id_for_url

logger = logging.getLogger(__name__)


class RssFetcher:
    def fetch(self, url: str, source_name: str) -> list[Article]:
        feed = feedparser.parse(url)
        articles = []
        now = datetime.now(timezone.utc)
        for entry in feed.entries:
            # spec-reviewer: 直接属性アクセスを使用（entry.link など）
            link = entry.link if hasattr(entry, "link") else ""
            if not link:
                continue
            published_at = self._parse_date(entry) or now
            article = Article(
                id=article_id_for_url(link),
                title=entry.title if hasattr(entry, "title") else "",
                url=link,
                source=source_name,
                content=entry.summary if hasattr(entry, "summary") else "",
                published_at=published_at,
                fetched_at=now,
            )
            articles.append(article)
        return articles

    def _parse_date(self, entry) -> datetime | None:
        for field in ("published", "updated", "created"):
            raw = getattr(entry, field, None)
            if raw:
                try:
                    dt = parsedate_to_datetime(raw)
                    return dt.astimezone(timezone.utc)
                except Exception as e:
                    # サイレント飲み込みを避けてデバッグ時の可観測性を確保する
                    logger.debug(
                        "Failed to parse date field '%s' value %r: %s", field, raw, e
                    )
        return None
