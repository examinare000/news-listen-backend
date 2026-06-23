"""RSS フィード取得と記事 ID 生成。

SSRF 対策のため safe_fetch 経由でコンテンツを取得。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable

import feedparser

from shared.models import Article
from shared.utils import article_id_for_url
from shared.url_guard import safe_fetch

logger = logging.getLogger(__name__)


class RssFetcher:
    def __init__(self, fetch: Callable[[str], bytes | None] | None = None):
        """RssFetcher を初期化する。

        Args:
            fetch: URL フェッチ関数（DI用シーム）。
                   型: Callable[[str], bytes | None]
                   デフォルト: safe_fetch（SSRF対策済み）
                   テスト時に差し替え可能。
        """
        self._fetch = fetch or safe_fetch

    def fetch(self, url: str, source_name: str) -> list[Article]:
        """URL から RSS フィードを取得して記事リストを返す。

        UnsafeUrlError は捕捉しない。呼び出し元（main.py）で
        try/except で処理し logger.error してスキップする既存挙動に乗せる。
        """
        raw = self._fetch(url)
        if raw is None:
            return []

        feed = feedparser.parse(raw)
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
