import time
import logging
import os

import pandas as pd
from stockstats import wrap
from typing import Annotated

from .config import get_config
from .tradestation_client import get_client

logger = logging.getLogger(__name__)


def _clean_dataframe(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize a stock DataFrame for stockstats: parse dates, drop invalid rows, fill price gaps."""
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
    data = data.dropna(subset=["Date"])

    price_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in data.columns]
    data[price_cols] = data[price_cols].apply(pd.to_numeric, errors="coerce")
    data = data.dropna(subset=["Close"])
    data[price_cols] = data[price_cols].ffill().bfill()

    return data


def load_ohlcv(symbol: str, curr_date: str) -> pd.DataFrame:
    """Fetch OHLCV data with caching, filtered to prevent look-ahead bias.

    Downloads 90 days of daily bars from TradeStation and caches per symbol.
    On subsequent calls the cache is reused. Rows after curr_date are
    filtered out so backtests never see future prices.
    """
    config = get_config()
    curr_date_dt = pd.to_datetime(curr_date)

    # Cache uses a fixed window (90 days to today) so one file per symbol
    today_date = pd.Timestamp.today()
    start_date = today_date - pd.Timedelta(days=90)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = today_date.strftime("%Y-%m-%d")

    os.makedirs(config["data_cache_dir"], exist_ok=True)
    data_file = os.path.join(
        config["data_cache_dir"],
        f"{symbol}-TS-data-{start_str}-{end_str}.csv",
    )

    if os.path.exists(data_file):
        data = pd.read_csv(data_file, on_bad_lines="skip", encoding="utf-8")
    else:
        try:
            client = get_client()
            bars_result = client.get_bars(
                symbol=symbol.upper(),
                interval=1,
                unit="Daily",
                bars_back=90,
            )
            bars = bars_result.get("Bars", []) if isinstance(bars_result, dict) else []

            if bars:
                rows = []
                for bar in bars:
                    ts = bar.get("TimeStamp", "")
                    try:
                        dt = pd.Timestamp(ts.replace("Z", "+00:00")).tz_localize(None)
                    except (ValueError, TypeError):
                        dt = pd.Timestamp(ts[:10])
                    rows.append({
                        "Date": dt.strftime("%Y-%m-%d"),
                        "Open": bar.get("Open", 0),
                        "High": bar.get("High", 0),
                        "Low": bar.get("Low", 0),
                        "Close": bar.get("Close", 0),
                        "Volume": bar.get("TotalVolume", 0),
                    })
                data = pd.DataFrame(rows)
            else:
                data = pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])

            data.to_csv(data_file, index=False, encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to fetch OHLCV from TradeStation for {symbol}: {e}")
            data = pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])

    data = _clean_dataframe(data)

    # Filter to curr_date to prevent look-ahead bias in backtesting
    data = data[data["Date"] <= curr_date_dt]

    return data


def filter_financials_by_date(data: pd.DataFrame, curr_date: str) -> pd.DataFrame:
    """Drop financial statement columns (fiscal period timestamps) after curr_date.

    Financial statements use fiscal period end dates as columns.
    Columns after curr_date represent future data and are removed to
    prevent look-ahead bias.
    """
    if not curr_date or data.empty:
        return data
    cutoff = pd.Timestamp(curr_date)
    mask = pd.to_datetime(data.columns, errors="coerce") <= cutoff
    return data.loc[:, mask]


class StockstatsUtils:
    @staticmethod
    def get_stock_stats(
        symbol: Annotated[str, "ticker symbol for the company"],
        indicator: Annotated[
            str, "quantitative indicators based off of the stock data for the company"
        ],
        curr_date: Annotated[
            str, "curr date for retrieving stock price data, YYYY-mm-dd"
        ],
    ):
        data = load_ohlcv(symbol, curr_date)
        df = wrap(data)
        df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
        curr_date_str = pd.to_datetime(curr_date).strftime("%Y-%m-%d")

        df[indicator]  # trigger stockstats to calculate the indicator
        matching_rows = df[df["Date"].str.startswith(curr_date_str)]

        if not matching_rows.empty:
            indicator_value = matching_rows[indicator].values[0]
            return indicator_value
        else:
            return "N/A: Not a trading day (weekend or holiday)"
