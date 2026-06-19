"""共有ユーティリティ関数。

複数モジュールで使われるロジックをここに集約し、DRY を保つ。
"""
from __future__ import annotations

import hashlib
import re


def slugify(value: str) -> str:
    """表示名から Firestore doc-id 用の slug を生成する。

    英数字以外を区切り `-` に畳み込み、小文字化する（例: "Wired (Technology)" → "wired-technology"）。
    featuredSites のドキュメントキーに使う。英数字が一切残らない場合は URL ハッシュ等の
    別キーを呼び出し側で用意する想定だが、現状の英語サイト名では発生しない。
    """
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return slug


def article_id_for_url(url: str) -> str:
    """記事 URL から決定論的な記事 ID を生成する。

    SHA-256 ハッシュの先頭 20 文字を使用。同一 URL に対して常に同じ ID を返す。
    FirestoreClient.article_id() と RssFetcher.article_id_for() の共通実装として
    ここに一元化し、将来のアルゴリズム変更時の不整合を防ぐ。
    """
    return hashlib.sha256(url.encode()).hexdigest()[:20]


def cache_key_for(article_id: str, difficulty: str, language: str) -> str:
    """クロスユーザー Podcast キャッシュの Firestore doc-id を生成する。

    形式: "{article_id}__{difficulty}__{language}"

    article_id（SHA-256 hex[:20]）と difficulty（DifficultyLevel Literal）は
    いずれも '__' を含まないため、セパレータで一意に分解できる。
    キャッシュキーの組み立てを散在させず、この関数に一元化する。
    """
    return f"{article_id}__{difficulty}__{language}"
