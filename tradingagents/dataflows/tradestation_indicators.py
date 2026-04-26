"""TradeStation technical indicators vendor implementation.

Computes technical indicators (RSI, MACD, Bollinger Bands, etc.) from
TradeStation REST API bar data using stockstats.

Uses the standalone TradeStationClient from tradestation_client module.
"""

from datetime import datetime
import pandas as pd
from stockstats import wrap

from .tradestation_client import get_client


def _bars_to_dataframe(bars: list[dict]) -> pd.DataFrame:
    """Convert TradeStation bar list to a stockstats-compatible DataFrame."""
    if not bars:
        return pd.DataFrame()

    records = []
    for bar in bars:
        ts = bar.get("TimeStamp", "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            date_str = dt.strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            date_str = ts[:10] if ts else "N/A"

        records.append({
            "Date": date_str,
            "Open": bar.get("Open", 0),
            "High": bar.get("High", 0),
            "Low": bar.get("Low", 0),
            "Close": bar.get("Close", 0),
            "Volume": bar.get("TotalVolume", 0),
        })

    df = pd.DataFrame(records)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)

    # Ensure numeric columns
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Forward/backward fill any NaN prices
    df[["Open", "High", "Low", "Close"]] = df[["Open", "High", "Low", "Close"]].ffill().bfill()

    return df


# Indicator descriptions matching yfinance/alpha_vantage conventions
_INDICATOR_DESCRIPTIONS = {
    "close_50_sma": (
        "50 SMA: A medium-term trend indicator. "
        "Usage: Identify trend direction and serve as dynamic support/resistance. "
        "Tips: It lags price; combine with faster indicators for timely signals."
    ),
    "close_200_sma": (
        "200 SMA: A long-term trend benchmark. "
        "Usage: Confirm overall market trend and identify golden/death cross setups. "
        "Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries."
    ),
    "close_10_ema": (
        "10 EMA: A responsive short-term average. "
        "Usage: Capture quick shifts in momentum and potential entry points. "
        "Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals."
    ),
    "macd": (
        "MACD: Computes momentum via differences of EMAs. "
        "Usage: Look for crossovers and divergence as signals of trend changes. "
        "Tips: Confirm with other indicators in low-volatility or sideways markets."
    ),
    "macds": (
        "MACD Signal: An EMA smoothing of the MACD line. "
        "Usage: Use crossovers with the MACD line to trigger trades. "
        "Tips: Should be part of a broader strategy to avoid false positives."
    ),
    "macdh": (
        "MACD Histogram: Shows the gap between the MACD line and its signal. "
        "Usage: Visualize momentum strength and spot divergence early. "
        "Tips: Can be volatile; complement with additional filters in fast-moving markets."
    ),
    "rsi": (
        "RSI: Measures momentum to flag overbought/oversold conditions. "
        "Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. "
        "Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis."
    ),
    "boll": (
        "Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. "
        "Usage: Acts as a dynamic benchmark for price movement. "
        "Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals."
    ),
    "boll_ub": (
        "Bollinger Upper Band: Typically 2 standard deviations above the middle line. "
        "Usage: Signals potential overbought conditions and breakout zones. "
        "Tips: Confirm signals with other tools; prices may ride the band in strong trends."
    ),
    "boll_lb": (
        "Bollinger Lower Band: Typically 2 standard deviations below the middle line. "
        "Usage: Indicates potential oversold conditions. "
        "Tips: Use additional analysis to avoid false reversal signals."
    ),
    "atr": (
        "ATR: Averages true range to measure volatility. "
        "Usage: Set stop-loss levels and adjust position sizes based on current market volatility. "
        "Tips: It's a reactive measure, so use it as part of a broader risk management strategy."
    ),
    "mfi": (
        "MFI: The Money Flow Index is a momentum indicator that uses both price and volume to measure buying and selling pressure. "
        "Usage: Identify overbought (>80) or oversold (<20) conditions and confirm the strength of trends or reversals. "
        "Tips: Use alongside RSI or MACD to confirm signals; divergence between price and MFI can indicate potential reversals."
    ),
}


def get_indicators(
    symbol: str,
    indicator: str,
    curr_date: str,
    look_back_days: int = 90,
) -> str:
    """Calculate technical indicators from TradeStation bar data.

    Matches the agent-facing signature: get_indicators(symbol, indicator, curr_date, look_back_days).
    For a single indicator, returns that indicator's values. If indicator is a comma-separated
    list of indicator names, returns all of them.

    Args:
        symbol: Ticker symbol (e.g. "NVDA")
        indicator: Indicator name (e.g. "rsi", "macd", or comma-separated list)
        curr_date: Current date in YYYY-MM-DD format
        look_back_days: Number of days to look back (default 90)

    Returns:
        Formatted string with indicator values and descriptions
    """
    from dateutil.relativedelta import relativedelta

    try:
        client = get_client()

        # Fetch enough bars for the lookback period
        bars_result = client.get_bars(
            symbol=symbol.upper(),
            interval=1,
            unit="Daily",
            bars_back=max(look_back_days + 30, 120),
        )
        bars = bars_result.get("Bars", []) if isinstance(bars_result, dict) else []

        if not bars:
            return f"No bar data available for {symbol.upper()} to calculate indicators."

        # Convert to DataFrame
        df = _bars_to_dataframe(bars)
        if df.empty:
            return f"No valid OHLCV data for {symbol.upper()} to calculate indicators."

        # Filter to date range
        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        before_dt = curr_dt - relativedelta(days=look_back_days)
        df = df[(df["Date"] >= before_dt) & (df["Date"] <= curr_dt)]

        if df.empty:
            return f"No data in the date range {before_dt.strftime('%Y-%m-%d')} to {curr_date}."

        # Wrap with stockstats
        df_wrapped = wrap(df.copy())

        # Parse indicator list (single or comma-separated)
        indicator_names = [i.strip() for i in indicator.split(",")]

        # Compute all requested indicators
        for ind in indicator_names:
            if ind in _INDICATOR_DESCRIPTIONS:
                _ = df_wrapped[ind]

        # Format output
        header = (
            f"# Technical Indicators for {symbol.upper()}\n"
            f"Period: {before_dt.strftime('%Y-%m-%d')} to {curr_date}\n"
            f"Data points: {len(df)}\n"
            f"Calculated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        )

        lines = []
        for ind in indicator_names:
            if ind not in _INDICATOR_DESCRIPTIONS:
                lines.append(f"## {ind.upper()}: NOT SUPPORTED")
                lines.append(f"Supported indicators: {list(_INDICATOR_DESCRIPTIONS.keys())}")
                lines.append("")
                continue

            if ind not in df_wrapped.columns:
                lines.append(f"## {ind.upper()}: N/A (not computed)")
                lines.append("")
                continue

            latest = df_wrapped[ind].iloc[-1]
            if pd.isna(latest) or latest is None:
                value_str = "N/A"
            else:
                value_str = f"{latest:.4f}"

            desc = _INDICATOR_DESCRIPTIONS.get(ind, "")
            lines.append(f"## {ind.upper()}: {value_str}")
            if desc:
                lines.append(desc)
                lines.append("")

        return header + "\n".join(lines)

    except Exception as e:
        return f"Error calculating indicators for {symbol}: {str(e)}"


def get_indicator(
    symbol: str,
    indicator: str,
    curr_date: str,
    look_back_days: int = 90,
) -> str:
    """Get a specific technical indicator for a date window.

    Matches the signature of alpha_vantage.get_indicator and
    yfinance.get_stock_stats_indicators_window for compatibility.

    Args:
        symbol: Ticker symbol
        indicator: Indicator name (e.g. "rsi", "macd", "boll")
        curr_date: Current date in YYYY-MM-DD format
        look_back_days: Number of days to look back

    Returns:
        Formatted string with indicator values for the date range
    """
    from dateutil.relativedelta import relativedelta

    if indicator not in _INDICATOR_DESCRIPTIONS:
        supported = list(_INDICATOR_DESCRIPTIONS.keys())
        return (
            f"Indicator '{indicator}' is not supported. "
            f"Supported indicators: {supported}"
        )

    try:
        client = get_client()

        # Fetch enough bars for the lookback period
        bars_result = client.get_bars(
            symbol=symbol.upper(),
            interval=1,
            unit="Daily",
            bars_back=max(look_back_days + 30, 120),
        )
        bars = bars_result.get("Bars", []) if isinstance(bars_result, dict) else []

        if not bars:
            return f"No bar data available for {symbol.upper()}."

        df = _bars_to_dataframe(bars)
        if df.empty:
            return f"No valid OHLCV data for {symbol.upper()}."

        # Filter to date range
        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        before_dt = curr_dt - relativedelta(days=look_back_days)
        df = df[(df["Date"] >= before_dt) & (df["Date"] <= curr_dt)]

        if df.empty:
            return f"No data in the date range {before_dt.strftime('%Y-%m-%d')} to {curr_date}."

        # Wrap with stockstats
        df_wrapped = wrap(df.copy())

        # Trigger calculation
        _ = df_wrapped[indicator]

        # Build output
        ind_string = ""
        for _, row in df_wrapped.iterrows():
            date_str = row["Date"].strftime("%Y-%m-%d")
            value = row[indicator]
            if pd.isna(value):
                value_str = "N/A"
            else:
                value_str = f"{value:.4f}"
            ind_string += f"{date_str}: {value_str}\n"

        result_str = (
            f"## {indicator.upper()} values from {before_dt.strftime('%Y-%m-%d')} "
            f"to {curr_date}:\n\n"
            + ind_string
            + "\n\n"
            + _INDICATOR_DESCRIPTIONS.get(indicator, "No description available.")
        )

        return result_str

    except Exception as e:
        return f"Error retrieving {indicator} for {symbol}: {str(e)}"
