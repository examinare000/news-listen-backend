"""trafilatura を使った記事本文エクストラクター。

SSRF 対策のため trafilatura.fetch_url を使わず safe_fetch 経由でコンテンツを取得。
"""
from __future__ import annotations

from typing import Callable

import trafilatura

from shared.url_guard import safe_fetch, UnsafeUrlError


class ContentExtractor:
    def __init__(self, fetch: Callable[[str], bytes | None] | None = None):
        """ContentExtractor を初期化する。

        Args:
            fetch: URL フェッチ関数（DI用シーム）。
                   型: Callable[[str], bytes | None]
                   デフォルト: safe_fetch（SSRF対策済み）
                   テスト時に差し替え可能。
        """
        self._fetch = fetch or safe_fetch

    def extract(self, url: str) -> str:
        """URL からコンテンツを取得して本文を抽出。

        UnsafeUrlError は捕捉して空文字を返す。
        既存の 'コンテンツ取得失敗=空文字' 挙動を維持し，
        main.py が落ちない契約を守る。
        """
        try:
            raw = self._fetch(url)
        except UnsafeUrlError:
            # SSRF 検査で危険と判定 → 空文字返す（既存挙動維持）
            return ""

        if raw is None:
            return ""

        # 生バイト列をそのまま trafilatura に渡す。utf-8 固定でデコードすると
        # Shift_JIS / EUC-JP など非 UTF-8 の日本語記事が文字化けするため、
        # trafilatura（lxml）側の文字コード自動判定に委ねる。
        text = trafilatura.extract(raw, include_comments=False, include_tables=False)
        return text or ""
