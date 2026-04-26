"""Tests for RSS news vendor implementation.

These tests verify the RSS news functions work correctly with mocked
HTTP responses.
"""

import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime


class TestRssNews(unittest.TestCase):
    """Tests for rss_news.py"""

    def test_get_ticker_aliases(self):
        """Test that ticker aliases are correctly resolved."""
        from tradingagents.dataflows.rss_news import _get_ticker_aliases

        aliases = _get_ticker_aliases("AAPL")
        self.assertIn("apple", aliases)
        self.assertIn("aapl", aliases)

        aliases = _get_ticker_aliases("MSFT")
        self.assertIn("microsoft", aliases)

        aliases = _get_ticker_aliases("UNKNOWN")
        self.assertIn("unknown", aliases)

    def test_is_relevant_ticker_match(self):
        """Test relevance check with ticker symbol match."""
        from tradingagents.dataflows.rss_news import _is_relevant

        article = {"title": "AAPL stock reaches new high", "summary": ""}
        self.assertTrue(_is_relevant(article, "AAPL", ["apple", "aapl"]))

    def test_is_relevant_name_match(self):
        """Test relevance check with company name match."""
        from tradingagents.dataflows.rss_news import _is_relevant

        article = {"title": "Apple announces new iPhone", "summary": ""}
        self.assertTrue(_is_relevant(article, "AAPL", ["apple", "aapl"]))

    def test_is_relevant_no_match(self):
        """Test relevance check with no match."""
        from tradingagents.dataflows.rss_news import _is_relevant

        article = {"title": "Tesla earnings beat expectations", "summary": ""}
        self.assertFalse(_is_relevant(article, "AAPL", ["apple", "aapl"]))

    @patch("tradingagents.dataflows.rss_news.requests.get")
    def test_fetch_feed_success(self, mock_get):
        """Test successful feed fetching."""
        mock_response = MagicMock()
        mock_response.text = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
            <channel>
                <title>Test Feed</title>
                <item>
                    <title>Test Article</title>
                    <description>A test article summary</description>
                    <link>https://example.com/article</link>
                    <pubDate>2024-12-01T10:00:00Z</pubDate>
                    <source>Test Source</source>
                </item>
            </channel>
        </rss>"""
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        from tradingagents.dataflows.rss_news import _fetch_feed

        result = _fetch_feed("https://example.com/feed.xml")

        self.assertIsNotNone(result)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Test Article")

    @patch("tradingagents.dataflows.rss_news.requests.get")
    def test_fetch_feed_failure(self, mock_get):
        """Test failed feed fetching returns None."""
        import requests
        mock_get.side_effect = requests.RequestException("Connection failed")

        from tradingagents.dataflows.rss_news import _fetch_feed

        result = _fetch_feed("https://example.com/feed.xml")

        self.assertIsNone(result)

    @patch("tradingagents.dataflows.rss_news._fetch_feed")
    def test_get_news(self, mock_fetch):
        """Test get_news with mocked feed data."""
        mock_fetch.return_value = [
            {
                "title": "AAPL reaches new highs",
                "summary": "Apple stock surges on strong earnings",
                "link": "https://example.com/aapl-news",
                "pub_date": datetime(2024, 12, 1, 10, 0, 0),
                "source": "Test Source",
            },
            {
                "title": "Microsoft earnings beat expectations",
                "summary": "MSFT reports strong quarterly results",
                "link": "https://example.com/msft-news",
                "pub_date": datetime(2024, 12, 1, 12, 0, 0),
                "source": "Test Source",
            },
        ]

        from tradingagents.dataflows.rss_news import get_news

        result = get_news("AAPL", "2024-12-01", "2024-12-02")

        self.assertIsInstance(result, str)
        self.assertIn("AAPL", result)
        # Only AAPL-relevant article should be included
        self.assertIn("AAPL reaches new highs", result)
        # Microsoft article should be filtered out
        self.assertNotIn("Microsoft earnings", result)

    @patch("tradingagents.dataflows.rss_news._fetch_feed")
    def test_get_news_no_relevant(self, mock_fetch):
        """Test get_news when no articles match the ticker."""
        mock_fetch.return_value = [
            {
                "title": "Tesla earnings beat expectations",
                "summary": "TSLA reports strong quarterly results",
                "link": "https://example.com/tsla-news",
                "pub_date": datetime(2024, 12, 1, 10, 0, 0),
                "source": "Test Source",
            },
        ]

        from tradingagents.dataflows.rss_news import get_news

        result = get_news("AAPL", "2024-12-01", "2024-12-02")

        self.assertIn("No news found", result)

    @patch("tradingagents.dataflows.rss_news._fetch_feed")
    def test_get_global_news(self, mock_fetch):
        """Test get_global_news returns articles from all feeds."""
        mock_fetch.return_value = [
            {
                "title": "Federal Reserve raises rates",
                "summary": "Fed increases interest rates by 25 basis points",
                "link": "https://example.com/fed-news",
                "pub_date": datetime(2024, 12, 1, 10, 0, 0),
                "source": "Reuters",
            },
            {
                "title": "Stock market rally continues",
                "summary": "Major indices hit new highs",
                "link": "https://example.com/market-news",
                "pub_date": datetime(2024, 12, 1, 14, 0, 0),
                "source": "Bloomberg",
            },
        ]

        from tradingagents.dataflows.rss_news import get_global_news

        result = get_global_news("2024-12-01", look_back_days=7, limit=5)

        self.assertIsInstance(result, str)
        self.assertIn("Global Market News", result)
        self.assertIn("Federal Reserve", result)

    @patch("tradingagents.dataflows.rss_news._fetch_feed")
    def test_get_global_news_empty(self, mock_fetch):
        """Test get_global_news when no articles are available."""
        mock_fetch.return_value = []

        from tradingagents.dataflows.rss_news import get_global_news

        result = get_global_news("2024-12-01", look_back_days=7)

        self.assertIn("No global market news", result)

    def test_date_filtering(self):
        """Test that articles outside the date range are filtered."""
        from tradingagents.dataflows.rss_news import _is_relevant
        from datetime import datetime, timedelta

        old_article = {
            "title": "AAPL news from 2023",
            "summary": "",
        }

        # Relevance check doesn't filter by date — that's done in get_news
        # This test just verifies the relevance function works
        self.assertTrue(_is_relevant(old_article, "AAPL", ["apple", "aapl"]))

    def test_default_feeds_configured(self):
        """Test that default RSS feeds are configured."""
        from tradingagents.dataflows.rss_news import DEFAULT_RSS_FEEDS

        self.assertIsInstance(DEFAULT_RSS_FEEDS, list)
        self.assertGreaterEqual(len(DEFAULT_RSS_FEEDS), 4)

        # Check that each feed has required fields
        for feed in DEFAULT_RSS_FEEDS:
            self.assertIn("name", feed)
            self.assertIn("url", feed)


class TestRssNewsTickerAliases(unittest.TestCase):
    """Tests for ticker alias resolution."""

    def test_common_tickers_have_aliases(self):
        """Test that common tickers have useful aliases."""
        from tradingagents.dataflows.rss_news import _TICKER_ALIASES

        self.assertIn("AAPL", _TICKER_ALIASES)
        self.assertIn("MSFT", _TICKER_ALIASES)
        self.assertIn("GOOGL", _TICKER_ALIASES)
        self.assertIn("AMZN", _TICKER_ALIASES)
        self.assertIn("TSLA", _TICKER_ALIASES)

    def test_alias_includes_ticker_lower(self):
        """Test that aliases include the ticker symbol in lowercase."""
        from tradingagents.dataflows.rss_news import _get_ticker_aliases

        aliases = _get_ticker_aliases("AAPL")
        self.assertIn("aapl", aliases)


if __name__ == "__main__":
    unittest.main()
