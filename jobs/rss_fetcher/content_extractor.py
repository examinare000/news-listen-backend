"""trafilatura を使った記事本文エクストラクター。"""
from __future__ import annotations

import trafilatura


class ContentExtractor:
    def extract(self, url: str) -> str:
        html = trafilatura.fetch_url(url)
        if html is None:
            return ""
        text = trafilatura.extract(html, include_comments=False, include_tables=False)
        return text or ""
