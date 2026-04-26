"""TradeStation stock data vendor implementation.

Provides OHLCV data and current quotes via the TradeStation REST API.
Uses the standalone TradeStationClient that reads credentials from
~/projects/tfresh2/config.ini and token.json.
"""

from datetime import datetime
from .tradestation_client import get_client


def get_stock_data(
    symbol: str,
    start_date: str,
    end_date: str,
) -> str:
    """Return OHLCV data as formatted string.

    Uses TradeStation REST API for historical bars and current quotes.

    Args:
        symbol: Ticker symbol (e.g. "AAPL")
        start_date: Start date in yyyy-mm-dd format
        end_date: End date in yyyy-mm-dd format

    Returns:
        Formatted string with OHLCV data and current quote
    """
    try:
        client = get_client()

        # Get symbol details for description
        desc = symbol.upper()
        try:
            details = client.get_symbol_details([symbol.upper()])
            if details and "Symbols" in details and details["Symbols"]:
                sym = details["Symbols"][0]
                desc = sym.get("Description", symbol.upper())
        except Exception:
            pass

        # Get historical bars (90 days)
        bars = []
        try:
            bars_result = client.get_bars(
                symbol=symbol.upper(),
                interval=1,
                unit="Daily",
                bars_back=90,
            )
            bars = bars_result.get("Bars", []) if isinstance(bars_result, dict) else []
        except Exception as e:
            print(f"[TradeStation] Failed to get bars for {symbol}: {e}")

        # Get current quote
        quotes = []
        try:
            quotes_result = client.get_quotes([symbol.upper()])
            quotes = quotes_result.get("Quotes", []) if isinstance(quotes_result, dict) else []
        except Exception as e:
            print(f"[TradeStation] Failed to get quote for {symbol}: {e}")

        # Build formatted output
        header = (
            f"# Stock data for {symbol.upper()} ({desc}) "
            f"from {start_date} to {end_date}\n"
            f"# Total records: {len(bars)}\n"
            f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        )

        # Format bars
        lines = ["Date,Open,High,Low,Close,Volume"]
        for bar in bars:
            ts = bar.get("TimeStamp", "")
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                date_str = dt.strftime("%Y-%m-%d")
            except (ValueError, AttributeError):
                date_str = ts[:10] if ts else "N/A"

            open_price = bar.get("Open", "N/A")
            high = bar.get("High", "N/A")
            low = bar.get("Low", "N/A")
            close = bar.get("Close", "N/A")
            volume = bar.get("TotalVolume", "N/A")

            lines.append(
                f"{date_str},{open_price},{high},{low},{close},{volume}"
            )

        csv_data = "\n".join(lines)

        # Append current quote
        quote_section = ""
        if quotes:
            q = quotes[0]
            quote_section = (
                f"\n\n# Current Quote for {symbol.upper()}:\n"
                f"Last: {q.get('Last', 'N/A')}\n"
                f"Bid: {q.get('Bid', 'N/A')} ({q.get('BidSize', '')})\n"
                f"Ask: {q.get('Ask', 'N/A')} ({q.get('AskSize', '')})\n"
                f"Open: {q.get('Open', 'N/A')}\n"
                f"High: {q.get('High', 'N/A')}\n"
                f"Low: {q.get('Low', 'N/A')}\n"
                f"Volume: {q.get('Volume', 'N/A')}\n"
                f"Net Change: {q.get('NetChange', 'N/A')} "
                f"({q.get('NetChangePct', 'N/A')}%)\n"
            )

        return header + csv_data + quote_section

    except Exception as e:
        return f"Error retrieving stock data for {symbol}: {str(e)}"


def get_insider_transactions(ticker: str) -> str:
    """TradeStation doesn't have insider transactions API.

    Args:
        ticker: Ticker symbol

    Returns:
        Placeholder string noting this limitation
    """
    return "Insider transaction data not available via TradeStation API."
