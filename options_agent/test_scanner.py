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
    direction, score, reasons, discarded = scanner._technical_score(quiet_df, ["ma_rsi", "bb_squeeze_breakout"])
    print(f"direction={direction} score={score} reasons={reasons} discarded={discarded}")
    assert direction in (0, 1, -1)
    assert 0 <= score <= 60

    print("\n=== 2. Technical scoring on a bar that deterministically crosses over (trend filter aligned) ===")
    crossover_df = make_df_ending_on_a_crossover()
    # the ramp pushes price well above its own 50-bar SMA, so the trend filter should align, not discard
    direction, score, reasons, discarded = scanner._technical_score(
        crossover_df, ["ma_rsi", "bb_squeeze_breakout"],
        trend_filter_period=config.TREND_FILTER_PERIOD, trend_filter_hard=True,
    )
    print(f"direction={direction} score={score}")
    for r in reasons:
        print(f"  - {r}")
    assert direction == 1, "expected the forced ramp to fire a bullish crossover"
    assert not discarded, "expected the post-ramp price to be aligned with its own 50-bar SMA, not discarded"
    assert score > 0 and reasons
    assert any("trend filter" in r for r in reasons), "expected a trend-filter reason since a period was passed"

    print("\n=== 3. Trend filter hard-discards a counter-trend swing signal ===")
    # flip direction convention: pretend the discovered signal is bearish while price sits
    # above its 50-bar SMA (the ramp made it so) -- this must be discarded, not just scored lower
    aligned, trend_reasons = scanner._trend_filter(crossover_df, direction=-1, period=config.TREND_FILTER_PERIOD)
    print(f"aligned={aligned} reasons={trend_reasons}")
    assert aligned is not None and not aligned, "a bearish call while price sits well above its 50-bar SMA should read as counter-trend"

    print("\n=== 4. Relative strength: outperformance vs benchmark is detected correctly ===")
    bench_df = make_trending_daily(days=200, seed=3, drift=0.0002)  # flat-ish benchmark
    strong_df = make_trending_daily(days=200, seed=3, drift=0.004)  # same seed, clearly stronger drift
    rs = scanner.relative_strength_excess(strong_df, bench_df, idx=-1, lookback=60)
    print(f"relative strength (outperformer vs flat benchmark): {rs:+.3f}")
    assert rs is not None and rs > 0, "a much stronger drift over the same window should read as positive excess return"
    bonus, reasons = scanner._relative_strength_bonus(1, rs)
    print(f"bonus={bonus} reasons={reasons}")
    assert bonus == config.RS_BONUS

    print("\n=== 5. backtestable_score adds the MTF confluence bonus when the daily trend agrees ===")
    direction, score_no_mtf, _, _ = scanner.backtestable_score(
        crossover_df, ["ma_rsi", "bb_squeeze_breakout"],
        trend_filter_period=config.INTRADAY_TREND_FILTER_PERIOD, trend_filter_hard=False,
    )
    direction2, score_with_mtf, mtf_reasons, _ = scanner.backtestable_score(
        crossover_df, ["ma_rsi", "bb_squeeze_breakout"],
        trend_filter_period=config.INTRADAY_TREND_FILTER_PERIOD, trend_filter_hard=False,
        daily_df=crossover_df,  # same series stands in for "the daily trend" here -- it's already trending up
    )
    print(f"without MTF: {score_no_mtf}, with MTF: {score_with_mtf}")
    assert direction == direction2 == 1
    assert score_with_mtf == score_no_mtf + config.MTF_CONFLUENCE_BONUS
    assert any("higher-timeframe" in r for r in mtf_reasons)

    print("\n=== 6. Full scan_ticker with fetch + news + earnings/sector stubbed (aligned bullish news) ===")
    scanner._fetch = lambda ticker, tf_cfg: crossover_df
    scanner.get_news_sentiment = lambda ticker, lookback: (0.4, ["Company beats on strong demand"])
    scanner.days_until_earnings = lambda ticker: None       # no real yfinance calls in this sandboxed test
    scanner.get_sector_etf = lambda ticker: None
    # lower the threshold here just to exercise the full scan_ticker plumbing deterministically
    # in this single-strategy-agreement fixture, rather than needing a two-strategy fixture.
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
    assert isinstance(result["summary"], str) and "confidence" in result["summary"].lower()

    print("\n--- risk score ---")
    print(f"risk_score={result['risk_score']} risk_level={result['risk_level']}")
    assert 1 <= result["risk_score"] <= 10
    assert result["risk_level"] in ("LOW", "MODERATE", "HIGH")
    for kind in ("shares", "option"):
        assert 1 <= result["risk"][kind]["score"] <= 10
        assert result["risk"][kind]["reasons"], f"{kind} risk should always have at least one reason"
    # a short-dated option should never read as LESS risky than holding the shares themselves
    assert result["risk"]["option"]["score"] >= result["risk"]["shares"]["score"], \
        "an option leg should never score as less risky than the equivalent shares position"

    print("\n--- exit plan ---")
    plan = result["exit_plan"]
    print(plan)
    assert plan["entry_price"] == result["spot"]
    assert plan["shares"]["profit_target"] > plan["entry_price"] > plan["shares"]["stop_loss"], \
        "bullish shares plan should have target above entry above stop"
    assert plan["shares"]["reward_risk_ratio"] is not None and plan["shares"]["reward_risk_ratio"] > 0
    assert "time_stop" in plan and "invalidation_rule" in plan
    assert "option" in plan and plan["option"]["profit_target"] > plan["option"]["entry_price"] > plan["option"]["stop_loss"]
    assert "expiration_date" in result["option_alt"], "option_alt must state a calendar expiration date, not just DTE"
    thesis = plan["option"]["price_thesis"]
    assert thesis["underlying_target"] == plan["shares"]["profit_target"]
    assert thesis["underlying_stop"] == plan["shares"]["stop_loss"]
    assert thesis["est_option_value_at_target"] > 0 and thesis["est_option_value_at_stop"] > 0

    print("\n=== 7. Conflicting news should reduce confidence vs aligned news ===")
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
