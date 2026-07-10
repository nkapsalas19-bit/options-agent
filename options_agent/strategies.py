"""
Candidate strategies. Each function takes a price DataFrame and strategy-specific
params, and returns the same DataFrame with a 'signal' column added:
    1  = enter call (bullish)
   -1  = enter put (bearish)
    0  = no signal
This is deliberately rule-based and inspectable -- no black box scoring.
"""
import pandas as pd
import numpy as np
from indicators import sma, rsi, bollinger_bands, opening_range


def ma_rsi_signals(df, params):
    df = df.copy()
    df["fast_ma"] = sma(df["close"], params["fast_ma"])
    df["slow_ma"] = sma(df["close"], params["slow_ma"])
    df["rsi"] = rsi(df["close"], params["rsi_period"])

    cross_up = (df["fast_ma"] > df["slow_ma"]) & (df["fast_ma"].shift(1) <= df["slow_ma"].shift(1))
    cross_down = (df["fast_ma"] < df["slow_ma"]) & (df["fast_ma"].shift(1) >= df["slow_ma"].shift(1))

    bullish = cross_up & (df["rsi"] < params["rsi_overbought"])
    bearish = cross_down & (df["rsi"] > params["rsi_oversold"])

    df["signal"] = 0
    df.loc[bullish, "signal"] = 1
    df.loc[bearish, "signal"] = -1
    return df


def bb_squeeze_breakout_signals(df, params):
    df = df.copy()
    upper, mid, lower, bandwidth = bollinger_bands(df["close"], params["bb_period"], params["bb_std"])
    df["bb_upper"], df["bb_mid"], df["bb_lower"], df["bandwidth"] = upper, mid, lower, bandwidth

    bw_rank = df["bandwidth"].rolling(params["squeeze_lookback"]).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1] * 100, raw=False
    )
    was_squeezed = bw_rank.shift(1) <= params["squeeze_percentile"]

    breakout_up = was_squeezed & (df["close"] > df["bb_upper"])
    breakout_down = was_squeezed & (df["close"] < df["bb_lower"])

    df["signal"] = 0
    df.loc[breakout_up, "signal"] = 1
    df.loc[breakout_down, "signal"] = -1
    return df


def orb_signals(df, params):
    df = opening_range(df, params["range_minutes"])
    avg_vol = df["volume"].rolling(20).mean()

    breakout_up = (df["close"] > df["or_high"]) & (df["volume"] > avg_vol * params["min_volume_mult"])
    breakout_down = (df["close"] < df["or_low"]) & (df["volume"] > avg_vol * params["min_volume_mult"])

    df["signal"] = 0
    df.loc[breakout_up, "signal"] = 1
    df.loc[breakout_down, "signal"] = -1
    return df


STRATEGY_FUNCS = {
    "ma_rsi": ma_rsi_signals,
    "bb_squeeze_breakout": bb_squeeze_breakout_signals,
    "orb": orb_signals,
}
