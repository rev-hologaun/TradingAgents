"""Local technical indicators calculated from OHLCV bars.

All indicators are computed using pure numpy/pandas — no external indicator
libraries (stockstats, talib, etc.). Data source is TradeStation OHLCV bars.

Supported indicators (column names match stockstats conventions for compatibility):
    - close_10_ema, close_50_sma, close_200_sma  (moving averages)
    - macd, macds, macdh  (MACD line, signal line, histogram)
    - rsi  (Relative Strength Index, 14-period)
    - boll, boll_ub, boll_lb  (Bollinger Bands 20-period, 2σ)
    - atr  (Average True Range, 14-period)
    - mfi  (Money Flow Index, 14-period)

Usage:
    >>> import pandas as pd
    >>> df = pd.DataFrame({"Close": [...], "High": [...], "Low": [...], "Volume": [...]})
    >>> result = compute_all_indicators(df)
    >>> result["rsi"].iloc[-1]  # latest RSI value
"""

import numpy as np
import pandas as pd


def _ensure_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure required columns exist with correct names.

    Args:
        df: DataFrame with columns Date, Open, High, Low, Close, Volume

    Returns:
        DataFrame with standardized OHLCV columns
    """
    result = df.copy()
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col not in result.columns:
            result[col] = 0.0
        result[col] = pd.to_numeric(result[col], errors="coerce")

    # Forward/backward fill any NaN prices (handles sparse bar data)
    result[["Open", "High", "Low", "Close"]] = (
        result[["Open", "High", "Low", "Close"]].ffill().bfill()
    )
    result["Volume"] = result["Volume"].fillna(0)
    return result


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI using Wilder's smoothing (DXR method).

    Args:
        close: Close price series
        period: Lookback period (default 14)

    Returns:
        RSI series (0-100)
    """
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def compute_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Compute MACD line, signal line, and histogram.

    Args:
        close: Close price series
        fast: Fast EMA period (default 12)
        slow: Slow EMA period (default 26)
        signal: Signal EMA period (default 9)

    Returns:
        (macd_line, signal_line, histogram)
    """
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_bollinger_bands(
    close: pd.Series,
    period: int = 20,
    num_std: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Compute Bollinger Bands.

    Args:
        close: Close price series
        period: SMA period (default 20)
        num_std: Number of standard deviations (default 2)

    Returns:
        (middle_band, upper_band, lower_band)
    """
    middle = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()
    upper = middle + (num_std * std)
    lower = middle - (num_std * std)
    return middle, upper, lower


def compute_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Compute Average True Range.

    True Range = max(H-L, abs(H-prevC), abs(L-prevC))
    ATR = EMA of True Range over period.

    Args:
        high: High price series
        low: Low price series
        close: Close price series
        period: ATR period (default 14)

    Returns:
        ATR series
    """
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = true_range.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    return atr


def compute_mfi(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Compute Money Flow Index.

    MFI uses price and volume to measure buying/selling pressure.
    Similar to RSI but incorporates volume.

    Args:
        high: High price series
        low: Low price series
        close: Close price series
        volume: Volume series
        period: MFI period (default 14)

    Returns:
        MFI series (0-100)
    """
    typical_price = (high + low + close) / 3.0
    raw_money_flow = typical_price * volume

    delta = typical_price.diff()
    positive_flow = raw_money_flow.where(delta > 0, 0.0)
    negative_flow = raw_money_flow.where(delta < 0, 0.0)

    avg_pos_flow = positive_flow.ewm(
        alpha=1.0 / period, min_periods=period, adjust=False
    ).mean()
    avg_neg_flow = negative_flow.ewm(
        alpha=1.0 / period, min_periods=period, adjust=False
    ).mean()

    money_ratio = avg_pos_flow / avg_neg_flow.replace(0, np.nan)
    mfi = 100.0 - (100.0 / (1.0 + money_ratio))
    return mfi


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all supported technical indicators for an OHLCV DataFrame.

    Adds columns matching stockstats column names for compatibility:
        close_10_ema, close_50_sma, close_200_sma
        macd, macds, macdh
        rsi
        boll, boll_ub, boll_lb
        atr
        mfi

    Args:
        df: DataFrame with Date, Open, High, Low, Close, Volume columns

    Returns:
        DataFrame with all indicator columns added
    """
    df = _ensure_ohlcv(df)

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    # Moving averages
    df["close_10_ema"] = close.ewm(span=10, adjust=False).mean()
    df["close_50_sma"] = close.rolling(window=50).mean()
    df["close_200_sma"] = close.rolling(window=200).mean()

    # MACD
    macd, macds, macdh = compute_macd(close)
    df["macd"] = macd
    df["macds"] = macds
    df["macdh"] = macdh

    # RSI
    df["rsi"] = compute_rsi(close)

    # Bollinger Bands
    boll, boll_ub, boll_lb = compute_bollinger_bands(close)
    df["boll"] = boll
    df["boll_ub"] = boll_ub
    df["boll_lb"] = boll_lb

    # ATR
    df["atr"] = compute_atr(high, low, close)

    # MFI
    df["mfi"] = compute_mfi(high, low, close, volume)

    return df
