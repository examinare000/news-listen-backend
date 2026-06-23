from unittest.mock import patch


def test_extract_returns_text_when_safe_fetch_succeeds():
    """safe_fetch から bytes を取得し trafilatura.extract で抽出。"""
    from jobs.rss_fetcher.content_extractor import ContentExtractor

    # safe_fetch を注入
    def mock_safe_fetch(url):
        return b"<html><body><p>Article content here.</p></body></html>"

    with patch("jobs.rss_fetcher.content_extractor.trafilatura.extract") as mock_extract:
        mock_extract.return_value = "Article content here."
        extractor = ContentExtractor(fetch=mock_safe_fetch)
        result = extractor.extract("https://example.com/article")
        assert result == "Article content here."


def test_extract_returns_empty_string_when_safe_fetch_returns_none():
    """safe_fetch が None の場合，空文字を返す。"""
    from jobs.rss_fetcher.content_extractor import ContentExtractor

    def mock_safe_fetch(url):
        return None

    extractor = ContentExtractor(fetch=mock_safe_fetch)
    result = extractor.extract("https://example.com/broken")
    assert result == ""


def test_extract_catches_unsafe_url_error():
    """safe_fetch が UnsafeUrlError を raise した場合，捕捉して空文字を返す。
    既存の 'コンテンツ取得失敗=空文字' 挙動を維持。
    """
    from jobs.rss_fetcher.content_extractor import ContentExtractor
    from shared.url_guard import UnsafeUrlError

    def mock_safe_fetch(url):
        raise UnsafeUrlError("private_ip")

    extractor = ContentExtractor(fetch=mock_safe_fetch)
    result = extractor.extract("http://169.254.169.254")
    assert result == ""


# === 既存テスト（backward compatibility） ===


def test_extract_returns_text_when_trafilatura_succeeds():
    """Backward compatibility: safe_fetch 注入で動作確認。"""
    from jobs.rss_fetcher.content_extractor import ContentExtractor

    # safe_fetch を注入
    def mock_safe_fetch(url):
        return b"<html><body><p>Article content here.</p></body></html>"

    with patch("jobs.rss_fetcher.content_extractor.trafilatura.extract") as mock_extract:
        mock_extract.return_value = "Article content here."
        extractor = ContentExtractor(fetch=mock_safe_fetch)
        result = extractor.extract("https://example.com/article")
        assert result == "Article content here."


def test_extract_returns_empty_string_when_fetch_fails():
    """Backward compatibility: safe_fetch が None を返す場合。"""
    from jobs.rss_fetcher.content_extractor import ContentExtractor

    def mock_safe_fetch(url):
        return None

    extractor = ContentExtractor(fetch=mock_safe_fetch)
    result = extractor.extract("https://example.com/broken")
    assert result == ""


def test_extract_returns_empty_string_when_extract_returns_none():
    """Backward compatibility: trafilatura.extract が None を返す場合。"""
    from jobs.rss_fetcher.content_extractor import ContentExtractor

    def mock_safe_fetch(url):
        return b"<html></html>"

    with patch("jobs.rss_fetcher.content_extractor.trafilatura.extract") as mock_extract:
        mock_extract.return_value = None
        extractor = ContentExtractor(fetch=mock_safe_fetch)
        result = extractor.extract("https://example.com/empty")
        assert result == ""


def test_extract_passes_raw_bytes_to_trafilatura_for_charset_detection():
    """生バイト列を trafilatura.extract にそのまま渡す契約を固定する。

    utf-8 固定でデコードすると Shift_JIS / EUC-JP の日本語記事が文字化けするため、
    文字コード判定は trafilatura（lxml）に委ねる。デコード済み str ではなく
    bytes が渡ることを検証する。
    """
    from jobs.rss_fetcher.content_extractor import ContentExtractor

    raw = "<html><body><p>日本語記事</p></body></html>".encode("shift_jis")

    def mock_safe_fetch(url):
        return raw

    with patch("jobs.rss_fetcher.content_extractor.trafilatura.extract") as mock_extract:
        mock_extract.return_value = "日本語記事"
        extractor = ContentExtractor(fetch=mock_safe_fetch)
        extractor.extract("https://example.com/jp")

        called_arg = mock_extract.call_args.args[0]
        # utf-8 デコードによる文字化け（str 化）ではなく生バイトが渡ること
        assert isinstance(called_arg, (bytes, bytearray))
        assert called_arg == raw


def test_extract_preserves_shift_jis_content_end_to_end():
    """Shift_JIS の本文がデコード時に文字化けしないことを実際の trafilatura で確認。"""
    from jobs.rss_fetcher.content_extractor import ContentExtractor

    body = "日本語のニュース記事です。" * 20
    raw = f"<html><body><p>{body}</p></body></html>".encode("shift_jis")

    extractor = ContentExtractor(fetch=lambda url: raw)
    result = extractor.extract("https://example.com/jp")

    # 文字化け（U+FFFD 置換文字）が混入していないこと
    assert "�" not in result
    assert "日本語のニュース記事です。" in result
