"""TradeStation fundamentals vendor implementation.

Uses TradeStation REST API for basic symbol metadata. TradeStation does NOT
provide rich fundamental data (balance sheets, income statements, cash flow),
so these return informative notes and suggest yfinance as fallback.

Uses the standalone TradeStationClient from tradestation_client module.
"""

from datetime import datetime
from .tradestation_client import get_client


def get_fundamentals(ticker: str) -> str:
    """Get company fundamentals overview from TradeStation.

    TradeStation provides limited fundamental data — mainly symbol metadata.

    Args:
        ticker: Ticker symbol

    Returns:
        Formatted string with available fundamentals or a note about limitations
    """
    try:
        client = get_client()

        # Get symbol details
        details_result = client.get_symbol_details([ticker.upper()])
        symbols = details_result.get("Symbols", []) if isinstance(details_result, dict) else []

        if not symbols:
            return (
                f"# Company Fundamentals for {ticker.upper()}\n"
                f"# Note: Could not retrieve symbol details from TradeStation.\n\n"
                f"Symbol: {ticker.upper()}\n"
                f"Description: Not available via TradeStation\n"
            )

        sym = symbols[0]
        desc = sym.get("Description", "N/A")
        exchange = sym.get("Exchange", "N/A")
        category = sym.get("Category", "N/A")

        header = (
            f"# Company Fundamentals for {ticker.upper()}\n"
            f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"# NOTE: TradeStation provides limited fundamental data.\n"
            f"# For detailed fundamentals (P/E, market cap, sector, etc.),\n"
            f"# use yfinance or Alpha Vantage.\n\n"
        )

        lines = [
            f"Symbol: {ticker.upper()}",
            f"Description: {desc}",
            f"Exchange: {exchange}",
            f"Category: {category}",
            "",
            "# TradeStation API Limitations:",
            "# - No market cap, P/E ratio, or valuation metrics",
            "# - No sector/industry classification beyond basic category",
            "# - No financial statement data (income, balance sheet, cash flow)",
            "",
            "# Recommended fallback: Use yfinance for rich fundamental data.",
            "# In default_config.py, set 'fundamental_data': 'yfinance' to use",
            "# yfinance as the default for fundamental data queries.",
        ]

        return header + "\n".join(lines)

    except Exception as e:
        return f"Error retrieving fundamentals for {ticker}: {str(e)}"


def get_balance_sheet(ticker: str, freq: str = "quarterly",
                      curr_date: str = None) -> str:
    """TradeStation does not provide balance sheet data.

    Args:
        ticker: Ticker symbol
        freq: Frequency ('annual' or 'quarterly') — not used
        curr_date: Current date — not used

    Returns:
        Note about the limitation and suggestion to use yfinance
    """
    return (
        f"# Balance Sheet for {ticker.upper()}\n"
        f"# Note: TradeStation API does not provide balance sheet data.\n"
        f"# Consider using yfinance or Alpha Vantage for financial statements.\n\n"
        f"Frequency: {freq}\n"
        f"Data available: No\n\n"
        f"# To use yfinance for fundamentals, set in config:\n"
        f'#   "fundamental_data": "yfinance"\n'
    )


def get_cashflow(ticker: str, freq: str = "quarterly",
                 curr_date: str = None) -> str:
    """TradeStation does not provide cash flow data.

    Args:
        ticker: Ticker symbol
        freq: Frequency ('annual' or 'quarterly') — not used
        curr_date: Current date — not used

    Returns:
        Note about the limitation and suggestion to use yfinance
    """
    return (
        f"# Cash Flow for {ticker.upper()}\n"
        f"# Note: TradeStation API does not provide cash flow data.\n"
        f"# Consider using yfinance or Alpha Vantage for financial statements.\n\n"
        f"Frequency: {freq}\n"
        f"Data available: No\n\n"
        f"# To use yfinance for fundamentals, set in config:\n"
        f'#   "fundamental_data": "yfinance"\n'
    )


def get_income_statement(ticker: str, freq: str = "quarterly",
                         curr_date: str = None) -> str:
    """TradeStation does not provide income statement data.

    Args:
        ticker: Ticker symbol
        freq: Frequency ('annual' or 'quarterly') — not used
        curr_date: Current date — not used

    Returns:
        Note about the limitation and suggestion to use yfinance
    """
    return (
        f"# Income Statement for {ticker.upper()}\n"
        f"# Note: TradeStation API does not provide income statement data.\n"
        f"# Consider using yfinance or Alpha Vantage for financial statements.\n\n"
        f"Frequency: {freq}\n"
        f"Data available: No\n\n"
        f"# To use yfinance for fundamentals, set in config:\n"
        f'#   "fundamental_data": "yfinance"\n'
    )
