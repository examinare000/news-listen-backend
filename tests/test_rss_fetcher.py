from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


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
    with patch("jobs.rss_fetcher.rss_fetcher.feedparser.parse") as mock_parse:
        mock_parse.return_value = MagicMock(
            entries=[
                _make_entry("Article A", "https://example.com/a"),
                _make_entry("Article B", "https://example.com/b"),
            ],
            bozo=False,
        )
        from jobs.rss_fetcher.rss_fetcher import RssFetcher
        fetcher = RssFetcher()
        articles = fetcher.fetch("https://example.com/rss", source_name="example")
        assert len(articles) == 2
        assert articles[0].title == "Article A"
        assert articles[0].source == "example"


def test_fetch_skips_entries_without_link():
    with patch("jobs.rss_fetcher.rss_fetcher.feedparser.parse") as mock_parse:
        no_link_entry = _make_entry("No Link", "")
        valid_entry = _make_entry("Valid", "https://example.com/valid")
        mock_parse.return_value = MagicMock(entries=[no_link_entry, valid_entry], bozo=False)

        from jobs.rss_fetcher.rss_fetcher import RssFetcher
        fetcher = RssFetcher()
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
