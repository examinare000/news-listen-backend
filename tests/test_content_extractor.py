from unittest.mock import patch


def test_extract_returns_text_when_trafilatura_succeeds():
    with patch("jobs.rss_fetcher.content_extractor.trafilatura.fetch_url") as mock_fetch, \
         patch("jobs.rss_fetcher.content_extractor.trafilatura.extract") as mock_extract:
        mock_fetch.return_value = "<html><body><p>Article content here.</p></body></html>"
        mock_extract.return_value = "Article content here."

        from jobs.rss_fetcher.content_extractor import ContentExtractor
        extractor = ContentExtractor()
        result = extractor.extract("https://example.com/article")
        assert result == "Article content here."


def test_extract_returns_empty_string_when_fetch_fails():
    with patch("jobs.rss_fetcher.content_extractor.trafilatura.fetch_url") as mock_fetch:
        mock_fetch.return_value = None

        from jobs.rss_fetcher.content_extractor import ContentExtractor
        extractor = ContentExtractor()
        result = extractor.extract("https://example.com/broken")
        assert result == ""


def test_extract_returns_empty_string_when_extract_returns_none():
    with patch("jobs.rss_fetcher.content_extractor.trafilatura.fetch_url") as mock_fetch, \
         patch("jobs.rss_fetcher.content_extractor.trafilatura.extract") as mock_extract:
        mock_fetch.return_value = "<html></html>"
        mock_extract.return_value = None

        from jobs.rss_fetcher.content_extractor import ContentExtractor
        extractor = ContentExtractor()
        result = extractor.extract("https://example.com/empty")
        assert result == ""
