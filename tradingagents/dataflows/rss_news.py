"""RSS news vendor implementation.

Fetches news from configured RSS feeds and filters by ticker relevance
and date range. Uses the `feedparser` library for RSS parsing.

Default feeds:
- Bloomberg Markets: https://feeds.bloomberg.com/markets/news.rss
- Reuters: https://www.reuters.com/feed
- CNBC Markets: https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114
- MarketWatch: https://feeds.a.dj.com/rss/RSSMarketsMain.xml

Install feedparser: pip install feedparser
"""

from datetime import datetime, timedelta
from typing import Optional

import requests

# Default RSS feeds
DEFAULT_RSS_FEEDS = [
    {
        "name": "Bloomberg Markets",
        "url": "https://feeds.bloomberg.com/markets/news.rss",
    },
    {
        "name": "Reuters",
        "url": "https://feeds.reuters.com/reuters/topNews",
    },
    {
        "name": "CNBC Markets",
        "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    },
    {
        "name": "MarketWatch",
        "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    },
]

# Additional feeds for broader market coverage
ADDITIONAL_FEEDS = [
    {
        "name": "WSJ Markets",
        "url": "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
    },
    {
        "name": "Financial Times",
        "url": "https://www.ft.com/rss/home",
    },
]

# Ticker-to-company-name mapping for better relevance matching
_TICKER_ALIASES = {
    "AAPL": ["apple", "appl", "aapl"],
    "MSFT": ["microsoft", "msft"],
    "GOOGL": ["google", "alphabet", "googl", "goog"],
    "AMZN": ["amazon", "amzn"],
    "TSLA": ["tesla", "tsla"],
    "META": ["facebook", "meta", "fb"],
    "NVDA": ["nvidia", "nvda"],
    "JPM": ["jpmorgan", "jpm"],
    "V": ["visa", "v"],
    "JNJ": ["johnson johnson", "jnj"],
    "WMT": ["walmart", "wmt"],
    "UNH": ["unitedhealth", "unh"],
    "MA": ["mastercard", "ma"],
    "PG": ["procter gamble", "pg"],
    "DIS": ["disney", "dis"],
    "BAC": ["bank of america", "bac"],
    "XOM": ["exxonmobil", "xom"],
    "NFLX": ["netflix", "nflx"],
    "CRM": ["salesforce", "crm"],
    "AMD": ["amd", "advanced micro devices"],
    "INTC": ["intel", "intc"],
    "PYPL": ["paypal", "pypl"],
    "SQ": ["square", "block", "sq"],
    "UBER": ["uber", "uber"],
    "ABNB": ["airbnb", "abnb"],
    "COIN": ["coinbase", "coin"],
    "SHOP": ["shopify", "shop"],
    "SNOW": ["snowflake", "snow"],
    "PLTR": ["palantir", "pltr"],
    "RIVN": ["rivian", "rivn"],
    "LCID": ["lucid", "lcid"],
    "NIO": ["nio", "nio"],
    "XPEV": ["xpeng", "xpev"],
    "LI": ["li auto", "li"],
}


def _fetch_feed(feed_url: str, timeout: int = 15) -> Optional[list]:
    """Fetch and parse a single RSS feed.

    Args:
        feed_url: RSS feed URL
        timeout: Request timeout in seconds

    Returns:
        List of article dicts, or None on failure
    """
    try:
        resp = requests.get(feed_url, timeout=timeout, headers={
            "User-Agent": "TradingAgents/1.0 (RSS News Fetcher)",
        })
        resp.raise_for_status()

        # Parse with feedparser if available
        try:
            import feedparser
            feed = feedparser.parse(resp.text)
            if feed.bozo:
                # Parse error but might still have entries
                pass

            entries = []
            for entry in feed.entries:
                title = entry.get("title", "").strip()
                summary = entry.get("summary", entry.get("description", "")).strip()
                link = entry.get("link", "")
                published = entry.get("published", entry.get("updated", ""))
                source = entry.get("source", {}).get("title", feed.feed.get("title", "Unknown"))

                # Parse publish date
                pub_date = None
                if published:
                    try:
                        pub_date = datetime.strptime(published[:19], "%Y-%m-%dT%H:%M:%S")
                    except (ValueError, TypeError):
                        try:
                            pub_date = datetime.strptime(published[:10], "%Y-%m-%d")
                        except (ValueError, TypeError):
                            pass

                entries.append({
                    "title": title,
                    "summary": summary[:500],  # Truncate long summaries
                    "link": link,
                    "pub_date": pub_date,
                    "source": source,
                })

            return entries
        except ImportError:
            # feedparser not available — do basic text parsing
            return _parse_rss_basic(resp.text, feed_url)

    except requests.RequestException as e:
        print(f"Warning: Failed to fetch feed {feed_url}: {e}")
        return None


def _parse_rss_basic(text: str, source_url: str) -> list:
    """Basic RSS parsing fallback when feedparser is not available.

    This is a very simplified parser — not production quality.
    """
    import re
    entries = []

    # Very basic extraction — look for title, link patterns
    titles = re.findall(r'<title[^>]*>([^<]+)</title>', text)
    links = re.findall(r'<link[^>]*>([^<]+)</link>', text)
    descs = re.findall(r'<description[^>]*>([^<]*)</description>', text, re.DOTALL)

    for i, title in enumerate(titles[:50]):
        title = title.strip()
        if not title or len(title) < 5:
            continue
        entries.append({
            "title": title,
            "summary": descs[i].strip()[:500] if i < len(descs) else "",
            "link": links[i].strip() if i < len(links) else "",
            "pub_date": None,
            "source": source_url.split("//")[-1].split("/")[0] if "://" in source_url else "Unknown",
        })

    return entries


def _is_relevant(article: dict, ticker: str, ticker_aliases: list) -> bool:
    """Check if an article is relevant to the given ticker.

    Args:
        article: Parsed article dict
        ticker: Ticker symbol (e.g. "AAPL")
        ticker_aliases: List of name variations for the ticker

    Returns:
        True if the article mentions the ticker or its aliases
    """
    text = f"{article.get('title', '')} {article.get('summary', '')}".lower()

    # Check for ticker symbol (case-insensitive)
    if ticker.upper().lower() in text:
        return True

    # Check for company name aliases
    for alias in ticker_aliases:
        if alias.lower() in text:
            return True

    return False


def _get_ticker_aliases(ticker: str) -> list:
    """Get known aliases for a ticker symbol."""
    ticker = ticker.upper()
    return _TICKER_ALIASES.get(ticker, [ticker.lower()])


def get_news(
    ticker: str,
    start_date: str,
    end_date: str,
) -> str:
    """Fetch news from RSS feeds relevant to the ticker.

    Args:
        ticker: Ticker symbol (e.g. "AAPL")
        start_date: Start date in yyyy-mm-dd format
        end_date: End date in yyyy-mm-dd format

    Returns:
        Formatted string with relevant news articles
    """
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError as e:
        return f"Error parsing dates: {e}. Use yyyy-mm-dd format."

    ticker_aliases = _get_ticker_aliases(ticker)
    all_articles = []
    seen_titles = set()

    # Fetch from all configured feeds
    feeds = DEFAULT_RSS_FEEDS + ADDITIONAL_FEEDS
    for feed in feeds:
        entries = _fetch_feed(feed["url"])
        if entries is None:
            continue

        for article in entries:
            # Filter by date range
            if article.get("pub_date"):
                pub_date = article["pub_date"]
                # Make naive if timezone-aware
                if hasattr(pub_date, "tzinfo") and pub_date.tzinfo is not None:
                    pub_date = pub_date.replace(tzinfo=None)

                if pub_date < start_dt or pub_date > end_dt + timedelta(days=1):
                    continue

            # Filter by relevance to ticker
            if not _is_relevant(article, ticker, ticker_aliases):
                continue

            # Deduplicate by title
            title = article.get("title", "")
            if title and title in seen_titles:
                continue
            if title:
                seen_titles.add(title)

            all_articles.append(article)

    # Sort by date (most recent first)
    all_articles.sort(
        key=lambda a: a.get("pub_date") or datetime.min,
        reverse=True,
    )

    # Format output
    if not all_articles:
        return (
            f"No news found for {ticker} between {start_date} and {end_date}.\n"
            f"Try using a broader date range or check if the ticker is valid."
        )

    news_str = f"## {ticker} News, from {start_date} to {end_date}:\n\n"
    for i, article in enumerate(all_articles[:20]):  # Limit to 20 articles
        title = article.get("title", "No title")
        publisher = article.get("source", article.get("publisher", "Unknown"))
        summary = article.get("summary", "")
        link = article.get("link", "")

        news_str += f"### {title} (source: {publisher})\n"
        if summary:
            news_str += f"{summary}\n"
        if link:
            news_str += f"Link: {link}\n"
        news_str += "\n"

    return news_str


def get_global_news(
    curr_date: str,
    look_back_days: int = 7,
    limit: int = 5,
) -> str:
    """Fetch general market news from RSS feeds.

    Args:
        curr_date: Current date in yyyy-mm-dd format
        look_back_days: Number of days to look back (default 7)
        limit: Maximum number of articles to return (default 5)

    Returns:
        Formatted string with global market news
    """
    try:
        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        start_dt = curr_dt - timedelta(days=look_back_days)
        start_date = start_dt.strftime("%Y-%m-%d")
    except ValueError as e:
        return f"Error parsing date: {e}. Use yyyy-mm-dd format."

    all_articles = []
    seen_titles = set()

    # Fetch from all configured feeds
    feeds = DEFAULT_RSS_FEEDS + ADDITIONAL_FEEDS
    for feed in feeds:
        entries = _fetch_feed(feed["url"])
        if entries is None:
            continue

        for article in entries:
            # Filter by date range
            if article.get("pub_date"):
                pub_date = article["pub_date"]
                if hasattr(pub_date, "tzinfo") and pub_date.tzinfo is not None:
                    pub_date = pub_date.replace(tzinfo=None)

                if pub_date < start_dt or pub_date > curr_dt + timedelta(days=1):
                    continue

            # Deduplicate by title
            title = article.get("title", "")
            if title and title in seen_titles:
                continue
            if title:
                seen_titles.add(title)

            all_articles.append(article)

    # Sort by date (most recent first)
    all_articles.sort(
        key=lambda a: a.get("pub_date") or datetime.min,
        reverse=True,
    )

    # Format output
    if not all_articles:
        return f"No global market news found for the period {start_date} to {curr_date}."

    news_str = f"## Global Market News, from {start_date} to {curr_date}:\n\n"
    for article in all_articles[:limit]:
        title = article.get("title", "No title")
        publisher = article.get("source", article.get("publisher", "Unknown"))
        summary = article.get("summary", "")
        link = article.get("link", "")

        news_str += f"### {title} (source: {publisher})\n"
        if summary:
            news_str += f"{summary}\n"
        if link:
            news_str += f"Link: {link}\n"
        news_str += "\n"

    return news_str
