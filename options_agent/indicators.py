"""Technical indicators used by the strategy candidates."""
import pandas as pd
import numpy as np


def sma(series, period):
    return series.rolling(period).mean()


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def bollinger_bands(series, period=20, num_std=2.0):
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    bandwidth = (upper - lower) / mid
    return upper, mid, lower, bandwidth


def atr(df, period=14):
    """Average True Range -- measures actual recent volatility (gap-aware,
    unlike a plain high-low range), used to size stops/targets off of real
    price movement instead of an arbitrary flat percentage."""
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def opening_range(df, range_minutes=15):
    """Computes the high/low of the first N minutes of each trading day.
    Assumes df index is intraday datetime, market open 9:30 ET."""
    df = df.copy()
    df["date"] = df.index.date
    or_high, or_low = {}, {}
    for date, group in df.groupby("date"):
        window = group.between_time("09:30", _add_minutes("09:30", range_minutes))
        if not window.empty:
            or_high[date] = window["high"].max()
            or_low[date] = window["low"].min()
    df["or_high"] = df["date"].map(or_high)
    df["or_low"] = df["date"].map(or_low)
    return df.drop(columns=["date"])


def _add_minutes(hhmm, minutes):
    h, m = map(int, hhmm.split(":"))
    total = h * 60 + m + minutes
    return f"{total // 60:02d}:{total % 60:02d}"
