from unittest.mock import patch, MagicMock
import pytest


def _make_entry(title, link, published="Mon, 01 Jan 2024 00:00:00 +0000", summary=""):
    entry = MagicMock()
    entry.title = title
    entry.link = link
    entry.summary = summary
    entry.published = published
    # spec-reviewer: direct attribute access を使う実装に合わせて get() も設定
    entry.get.side_effect = lambda key, default="": {
        "link": link,
        "title": title,
        "summary": summary,
        "published": published,
    }.get(key, default)
    return entry


def test_fetch_returns_articles_for_valid_feed():
    """Backward compatibility: safe_fetch 注入で動作確認。"""
    with patch("jobs.rss_fetcher.rss_fetcher.feedparser.parse") as mock_parse:
        from jobs.rss_fetcher.rss_fetcher import RssFetcher

        def mock_safe_fetch(url):
            return b"<rss><channel></channel></rss>"

        mock_parse.return_value = MagicMock(
            entries=[
                _make_entry("Article A", "https://example.com/a"),
                _make_entry("Article B", "https://example.com/b"),
            ],
            bozo=False,
        )
        fetcher = RssFetcher(fetch=mock_safe_fetch)
        articles = fetcher.fetch("https://example.com/rss", source_name="example")
        assert len(articles) == 2
        assert articles[0].title == "Article A"
        assert articles[0].source == "example"


def test_fetch_skips_entries_without_link():
    """Backward compatibility: safe_fetch 注入で動作確認。"""
    with patch("jobs.rss_fetcher.rss_fetcher.feedparser.parse") as mock_parse:
        from jobs.rss_fetcher.rss_fetcher import RssFetcher

        def mock_safe_fetch(url):
            return b"<rss><channel></channel></rss>"

        no_link_entry = _make_entry("No Link", "")
        valid_entry = _make_entry("Valid", "https://example.com/valid")
        mock_parse.return_value = MagicMock(entries=[no_link_entry, valid_entry], bozo=False)

        fetcher = RssFetcher(fetch=mock_safe_fetch)
        articles = fetcher.fetch("https://example.com/rss", source_name="example")
        assert len(articles) == 1
        assert articles[0].title == "Valid"


def test_parse_date_returns_none_for_malformed_date():
    """不正な日付文字列は例外を飲み込まず None を返すことを確認する。"""
    from jobs.rss_fetcher.rss_fetcher import RssFetcher

    fetcher = RssFetcher()
    entry = MagicMock()
    entry.published = "not-a-valid-date"
    entry.updated = None
    entry.created = None

    result = fetcher._parse_date(entry)
    assert result is None


def test_parse_date_logs_debug_on_parse_failure(caplog):
    """_parse_date が不正な日付でパースに失敗した際に DEBUG ログを出すことを確認する。
    サイレントな例外飲み込みを防ぎ、デバッグ時の可観測性を保つ。
    """
    import logging
    from jobs.rss_fetcher.rss_fetcher import RssFetcher

    fetcher = RssFetcher()
    entry = MagicMock()
    entry.published = "THIS-IS-NOT-A-DATE"
    entry.updated = None
    entry.created = None

    with caplog.at_level(logging.DEBUG, logger="jobs.rss_fetcher.rss_fetcher"):
        result = fetcher._parse_date(entry)

    assert result is None
    # logger.debug が呼ばれてパース失敗が記録されていること
    assert any(
        "THIS-IS-NOT-A-DATE" in record.message or "parse" in record.message.lower()
        for record in caplog.records
    ), f"Expected debug log for parse failure, got: {[r.message for r in caplog.records]}"


# === safe_fetch DI テスト ===


def test_fetch_with_safe_fetch_injection():
    """safe_fetch を注入して RSS フィード取得。"""
    from jobs.rss_fetcher.rss_fetcher import RssFetcher

    def mock_safe_fetch(url):
        # feedparser が parse できる XML/RSS を返す
        return b"""<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Test Article</title>
      <link>https://example.com/test</link>
      <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""

    fetcher = RssFetcher(fetch=mock_safe_fetch)
    articles = fetcher.fetch("https://example.com/rss", source_name="test_source")
    assert len(articles) == 1
    assert articles[0].title == "Test Article"
    assert articles[0].url == "https://example.com/test"


def test_fetch_returns_empty_list_when_safe_fetch_returns_none():
    """safe_fetch が None を返す場合，空リストを返す。"""
    from jobs.rss_fetcher.rss_fetcher import RssFetcher

    def mock_safe_fetch(url):
        return None

    fetcher = RssFetcher(fetch=mock_safe_fetch)
    articles = fetcher.fetch("https://example.com/rss", source_name="test_source")
    assert articles == []


def test_fetch_raises_unsafe_url_error_to_caller():
    """safe_fetch が UnsafeUrlError を raise した場合，捕捉しない（呼び出し元で処理）。"""
    from jobs.rss_fetcher.rss_fetcher import RssFetcher
    from shared.url_guard import UnsafeUrlError

    def mock_safe_fetch(url):
        raise UnsafeUrlError("private_ip")

    fetcher = RssFetcher(fetch=mock_safe_fetch)
    # UnsafeUrlError は raise される（捕捉しない）
    with pytest.raises(UnsafeUrlError) as exc_info:
        fetcher.fetch("http://169.254.169.254/rss", source_name="test_source")
    assert exc_info.value.reason == "private_ip"


def test_feedparser_receives_bytes():
    """feedparser.parse が bytes を受け取ることを確認（URL文字列ではなく）。"""
    with patch("jobs.rss_fetcher.rss_fetcher.feedparser.parse") as mock_parse:
        from jobs.rss_fetcher.rss_fetcher import RssFetcher

        def mock_safe_fetch(url):
            return b"<rss><channel><item><title>Test</title><link>https://test.com</link></item></channel></rss>"

        mock_parse.return_value = MagicMock(entries=[], bozo=False)

        fetcher = RssFetcher(fetch=mock_safe_fetch)
        fetcher.fetch("https://example.com/rss", source_name="test")

        # feedparser.parse が bytes 引数で呼ばれたことを確認
        mock_parse.assert_called_once()
        call_args = mock_parse.call_args[0][0]
        assert isinstance(call_args, bytes), f"Expected bytes, got {type(call_args)}"
