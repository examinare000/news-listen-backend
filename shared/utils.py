"""共有ユーティリティ関数。

複数モジュールで使われるロジックをここに集約し、DRY を保つ。
"""
from __future__ import annotations

import hashlib


def article_id_for_url(url: str) -> str:
    """記事 URL から決定論的な記事 ID を生成する。

    SHA-256 ハッシュの先頭 20 文字を使用。同一 URL に対して常に同じ ID を返す。
    FirestoreClient.article_id() と RssFetcher.article_id_for() の共通実装として
    ここに一元化し、将来のアルゴリズム変更時の不整合を防ぐ。
    """
    return hashlib.sha256(url.encode()).hexdigest()[:20]
