"""shared.utils のユニットテスト。"""
from shared.utils import article_id_for_url


def test_article_id_is_sha256_hex_prefix_20chars():
    """SHA256[:20] の 16 進文字列であることを確認。"""
    result = article_id_for_url("https://example.com/article")
    assert len(result) == 20
    assert all(c in "0123456789abcdef" for c in result)


def test_article_id_is_deterministic():
    id1 = article_id_for_url("https://example.com/article")
    id2 = article_id_for_url("https://example.com/article")
    assert id1 == id2


def test_article_id_differs_for_different_urls():
    id1 = article_id_for_url("https://example.com/a")
    id2 = article_id_for_url("https://example.com/b")
    assert id1 != id2


def test_article_id_matches_legacy_firestore_logic():
    """FirestoreClient.article_id() / RssFetcher.article_id_for() との互換性。
    実装が共通関数に移行した後もハッシュ値が変わらないことを保証する。
    """
    import hashlib
    url = "https://news.ycombinator.com/item?id=12345"
    expected = hashlib.sha256(url.encode()).hexdigest()[:20]
    assert article_id_for_url(url) == expected
