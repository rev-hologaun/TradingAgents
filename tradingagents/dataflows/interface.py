from typing import Annotated

# TradeStation vendors
from .tradestation_stock import (
    get_stock_data as get_tradestation_stock_data,
    get_insider_transactions as get_tradestation_insider_transactions,
)
from .tradestation_indicators import (
    get_indicators as get_tradestation_indicators,
    get_indicator as get_tradestation_indicator,
)
from .tradestation_fundamentals import (
    get_fundamentals as get_tradestation_fundamentals,
    get_balance_sheet as get_tradestation_balance_sheet,
    get_cashflow as get_tradestation_cashflow,
    get_income_statement as get_tradestation_income_statement,
)

# Local fundamentals (SEC EDGAR scraper — replaces Alpha Vantage)
from .local_fundamentals import (
    get_fundamentals as get_local_fundamentals,
    get_balance_sheet as get_local_balance_sheet,
    get_cashflow as get_local_cashflow,
    get_income_statement as get_local_income_statement,
)

# RSS News vendor
from .rss_news import get_news as get_rss_news, get_global_news as get_rss_global_news

# Configuration and routing logic
from .config import get_config

# Tools organized by category
TOOLS_CATEGORIES = {
    "core_stock_apis": {
        "description": "OHLCV stock price data",
        "tools": [
            "get_stock_data"
        ]
    },
    "technical_indicators": {
        "description": "Technical analysis indicators",
        "tools": [
            "get_indicators"
        ]
    },
    "fundamental_data": {
        "description": "Company fundamentals",
        "tools": [
            "get_fundamentals",
            "get_balance_sheet",
            "get_cashflow",
            "get_income_statement"
        ]
    },
    "news_data": {
        "description": "News and insider data",
        "tools": [
            "get_news",
            "get_global_news",
            "get_insider_transactions",
        ]
    }
}

VENDOR_LIST = [
    "local_fundamentals",
    "tradestation",
    "rss",
]

# Mapping of methods to their vendor-specific implementations
VENDOR_METHODS = {
    # core_stock_apis
    "get_stock_data": {
        "tradestation": get_tradestation_stock_data,
    },
    # technical_indicators
    "get_indicators": {
        "tradestation": get_tradestation_indicators,
    },
    # fundamental_data — local_fundamentals (SEC EDGAR) is primary, TradeStation is fallback
    "get_fundamentals": {
        "local_fundamentals": get_local_fundamentals,
        "tradestation": get_tradestation_fundamentals,
    },
    "get_balance_sheet": {
        "local_fundamentals": get_local_balance_sheet,
        "tradestation": get_tradestation_balance_sheet,
    },
    "get_cashflow": {
        "local_fundamentals": get_local_cashflow,
        "tradestation": get_tradestation_cashflow,
    },
    "get_income_statement": {
        "local_fundamentals": get_local_income_statement,
        "tradestation": get_tradestation_income_statement,
    },
    # news_data
    "get_news": {
        "rss": get_rss_news,
    },
    "get_global_news": {
        "rss": get_rss_global_news,
    },
    "get_insider_transactions": {
        "tradestation": get_tradestation_insider_transactions,
    },
}

def get_category_for_method(method: str) -> str:
    """Get the category that contains the specified method."""
    for category, info in TOOLS_CATEGORIES.items():
        if method in info["tools"]:
            return category
    raise ValueError(f"Method '{method}' not found in any category")

def get_vendor(category: str, method: str = None) -> str:
    """Get the configured vendor for a data category or specific tool method.
    Tool-level configuration takes precedence over category-level.
    """
    config = get_config()

    # Check tool-level configuration first (if method provided)
    if method:
        tool_vendors = config.get("tool_vendors", {})
        if method in tool_vendors:
            return tool_vendors[method]

    # Fall back to category-level configuration
    return config.get("data_vendors", {}).get(category, "default")

def route_to_vendor(method: str, *args, **kwargs):
    """Route method calls to appropriate vendor implementation with fallback support."""
    category = get_category_for_method(method)
    vendor_config = get_vendor(category, method)
    primary_vendors = [v.strip() for v in vendor_config.split(',')]

    if method not in VENDOR_METHODS:
        raise ValueError(f"Method '{method}' not supported")

    # Build fallback chain: primary vendors first, then remaining available vendors
    all_available_vendors = list(VENDOR_METHODS[method].keys())
    fallback_vendors = primary_vendors.copy()
    for vendor in all_available_vendors:
        if vendor not in fallback_vendors:
            fallback_vendors.append(vendor)

    for vendor in fallback_vendors:
        if vendor not in VENDOR_METHODS[method]:
            continue

        vendor_impl = VENDOR_METHODS[method][vendor]
        impl_func = vendor_impl[0] if isinstance(vendor_impl, list) else vendor_impl

        try:
            return impl_func(*args, **kwargs)
        except Exception:
            continue

    raise RuntimeError(f"No available vendor for '{method}'")
