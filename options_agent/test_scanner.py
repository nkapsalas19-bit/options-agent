"""
Validates scanner.py's scoring/callout logic using synthetic price data and a
stubbed news source, since this sandbox can't reach Yahoo Finance for either
real prices or real news. Proves the scoring pipeline runs end-to-end and
produces well-formed callouts -- it says nothing about whether the resulting
signals are actually profitable. Run market_scanner.py on your own machine
(with real internet access) to see it against live data.
"""
import numpy as np
import pandas as pd

import scanner
from strategies import ma_rsi_signals
import config


def make_trending_daily(days=200, seed=7, drift=0.006):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp.today(), periods=days)
    returns = rng.normal(drift, 0.01, days)
    prices = 100 * np.cumprod(1 + returns)
    high = prices * (1 + np.abs(rng.normal(0, 0.003, days)))
    low = prices * (1 - np.abs(rng.normal(0, 0.003, days)))
    open_ = prices * (1 + rng.normal(0, 0.002, days))
    volume = rng.integers(1_000_000, 5_000_000, days)
    volume[-1] = int(volume[:-1].mean() * 2)  # force a volume-confirmation bar at the end
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": prices, "volume": volume}, index=dates)


def make_df_ending_on_a_crossover(days=200, seed=7):
    """Flat-then-ramp price series, sliced to end exactly on the bar where
    ma_rsi's fast/slow MA crossover fires, so the "a signal actually fired"
    path is exercised deterministically instead of hoping a random walk
    happens to cross on its very last bar."""
    df = make_trending_daily(days=days, seed=seed, drift=0.0)
    # flat regime, then a strong ramp in the back third to force a clean crossover
    ramp_start = int(days * 0.66)
    ramp = np.linspace(0, 25, days - ramp_start)
    df.loc[df.index[ramp_start:], "close"] = df["close"].iloc[ramp_start] * (1 + ramp / 100)
    df["high"] = df["close"] * 1.002
    df["low"] = df["close"] * 0.998
    df["open"] = df["close"]

    signaled = ma_rsi_signals(df, config.STRATEGIES["ma_rsi"])
    bullish_positions = np.flatnonzero(signaled["signal"].to_numpy() == 1)
    assert len(bullish_positions) > 0, "test fixture failed to produce a bullish crossover -- adjust the ramp"
    return df.iloc[: bullish_positions[-1] + 1]


def main():
    print("=== 1. Technical scoring when nothing fires (should be a clean no-op) ===")
    quiet_df = make_trending_daily(drift=0.0001, seed=99)
    direction, score, reasons = scanner._technical_score(quiet_df, ["ma_rsi", "bb_squeeze_breakout"])
    print(f"direction={direction} score={score} reasons={reasons}")
    assert direction in (0, 1, -1)
    assert 0 <= score <= 60

    print("\n=== 2. Technical scoring on a bar that deterministically crosses over ===")
    crossover_df = make_df_ending_on_a_crossover()
    direction, score, reasons = scanner._technical_score(crossover_df, ["ma_rsi", "bb_squeeze_breakout"])
    print(f"direction={direction} score={score} reasons={reasons}")
    assert direction == 1, "expected the forced ramp to fire a bullish crossover"
    assert score > 0 and reasons

    print("\n=== 3. Full scan_ticker with fetch + news stubbed (aligned bullish news) ===")
    scanner._fetch = lambda ticker, tf_cfg: crossover_df
    scanner.get_news_sentiment = lambda ticker, lookback: (0.4, ["Company beats on strong demand"])
    # only one strategy fires in this fixture (tech score 20) + aligned news (~16) = ~36,
    # below the real MIN_CONFIDENCE_SCORE=60 default -- lower it here just to exercise the
    # full scan_ticker plumbing end-to-end without needing a two-strategy-agreement fixture.
    original_min_confidence = config.MIN_CONFIDENCE_SCORE
    config.MIN_CONFIDENCE_SCORE = 10

    result = scanner.scan_ticker("TEST", "swing")
    print(result)
    assert result is not None, "expected a callout given a forced crossover + aligned bullish news"
    assert result["ticker"] == "TEST"
    assert result["timeframe"] == "swing"
    assert 0 <= result["confidence_score"] <= 100
    assert isinstance(result["reasons"], list) and result["reasons"]
    assert result["suggested_action"] == "BUY SHARES"

    print("\n=== 4. Conflicting news should reduce confidence vs aligned news ===")
    scanner.get_news_sentiment = lambda ticker, lookback: (-0.4, ["Company faces headwinds"])
    conflicting_result = scanner.scan_ticker("TEST", "swing")
    if conflicting_result is not None:
        assert conflicting_result["confidence_score"] < result["confidence_score"]
        print(f"aligned={result['confidence_score']} conflicting={conflicting_result['confidence_score']} (OK, conflict scored lower)")
    else:
        print("conflicting news pushed confidence below MIN_CONFIDENCE_SCORE -- discarded, as expected (still lower, just clipped)")

    config.MIN_CONFIDENCE_SCORE = original_min_confidence

    print("\n=== SCANNER VALIDATION: PASSED ===")


if __name__ == "__main__":
    main()
