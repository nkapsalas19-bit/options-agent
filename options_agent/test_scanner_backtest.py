"""
Validates scanner_backtest.py's trade-simulation and reporting math using
constructed OHLC series with known outcomes, since this sandbox can't reach
Yahoo Finance for real history. Proves the mechanics are correct (target
hits are scored as wins, stops as losses, same-bar ties resolve
conservatively to the stop, calibration buckets/aggregates compute the
right numbers) -- it says nothing about whether the scanner is actually
profitable on real markets. Run `python scanner_backtest.py` on your own
machine for that.
"""
import numpy as np
import pandas as pd

import config
import scanner_backtest as sb


def _flat_df(n=40, price=100.0):
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame({
        "open": price, "high": price, "low": price, "close": price, "volume": 1_000_000,
    }, index=idx)


def main():
    print("=== 1. _simulate_trade: target hit before stop (bullish) ===")
    df = _flat_df()
    entry_idx = 10
    atr_val = 1.0  # stop = 100 - 1.5 = 98.5; target = 100 + 2.5 = 102.5
    df.loc[df.index[entry_idx + 1], ["high", "low", "close"]] = [103.0, 99.5, 102.6]  # touches target, not stop
    outcome = sb._simulate_trade(df, entry_idx, direction=1, atr_val=atr_val, max_hold_bars=5)
    print(outcome)
    assert outcome["outcome"] == "target"
    assert outcome["r_multiple"] == config.ATR_TARGET_MULT / config.ATR_STOP_MULT

    print("\n=== 2. _simulate_trade: stop hit before target (bullish) ===")
    df2 = _flat_df()
    df2.loc[df2.index[entry_idx + 1], ["high", "low", "close"]] = [100.2, 98.0, 98.1]  # touches stop only
    outcome2 = sb._simulate_trade(df2, entry_idx, direction=1, atr_val=atr_val, max_hold_bars=5)
    print(outcome2)
    assert outcome2["outcome"] == "stop"
    assert outcome2["r_multiple"] == -1.0

    print("\n=== 3. _simulate_trade: same-bar tie resolves to the stop (conservative) ===")
    df3 = _flat_df()
    df3.loc[df3.index[entry_idx + 1], ["high", "low", "close"]] = [103.0, 98.0, 100.5]  # both touched same bar
    outcome3 = sb._simulate_trade(df3, entry_idx, direction=1, atr_val=atr_val, max_hold_bars=5)
    print(outcome3)
    assert outcome3["outcome"] == "stop", "a same-bar tie must resolve to the stop, not the target"

    print("\n=== 4. _simulate_trade: neither level hits -> time-stop at max_hold_bars ===")
    df4 = _flat_df()
    # price drifts up slightly but never reaches target or stop
    for i in range(entry_idx + 1, entry_idx + 6):
        df4.loc[df4.index[i], ["high", "low", "close"]] = [100.5, 99.8, 100.3]
    outcome4 = sb._simulate_trade(df4, entry_idx, direction=1, atr_val=atr_val, max_hold_bars=5)
    print(outcome4)
    assert outcome4["outcome"] == "time_stop"
    assert outcome4["exit_idx"] == entry_idx + 5
    assert -1.0 < outcome4["r_multiple"] < config.ATR_TARGET_MULT / config.ATR_STOP_MULT

    print("\n=== 5. _simulate_trade: bearish direction mirrors correctly ===")
    df5 = _flat_df()
    df5.loc[df5.index[entry_idx + 1], ["high", "low", "close"]] = [100.5, 97.0, 97.3]  # price drops -> target for a short
    outcome5 = sb._simulate_trade(df5, entry_idx, direction=-1, atr_val=atr_val, max_hold_bars=5)
    print(outcome5)
    assert outcome5["outcome"] == "target"

    print("\n=== 6. aggregate_results: known win/loss mix produces the right stats ===")
    trades = [
        {"outcome": "target", "r_multiple": 1.67},
        {"outcome": "target", "r_multiple": 1.67},
        {"outcome": "stop", "r_multiple": -1.0},
        {"outcome": "stop", "r_multiple": -1.0},
        {"outcome": "time_stop", "r_multiple": 0.2},
    ]
    stats = sb.aggregate_results(trades)
    print(stats)
    assert stats["trades"] == 5
    assert stats["win_rate"] == round(3 / 5, 3)  # 2 targets + 1 positive time_stop
    expected_expectancy = sum(t["r_multiple"] for t in trades) / 5
    assert abs(stats["expectancy_r"] - expected_expectancy) < 1e-9

    print("\n=== 7. calibration_table: buckets trades by tech_score correctly ===")
    scored_trades = [
        {"tech_score": 22, "r_multiple": 1.67, "outcome": "target"},
        {"tech_score": 28, "r_multiple": -1.0, "outcome": "stop"},
        {"tech_score": 55, "r_multiple": 1.67, "outcome": "target"},
        {"tech_score": 58, "r_multiple": 1.67, "outcome": "target"},
    ]
    table = sb.calibration_table(scored_trades, "swing", bucket_size=10)
    print(table)
    buckets = {row["score_bucket"]: row for row in table}
    assert "20-29" in buckets and buckets["20-29"]["trades"] == 2
    assert "50-59" in buckets and buckets["50-59"]["trades"] == 2
    assert buckets["50-59"]["win_rate"] == 1.0
    assert buckets["20-29"]["win_rate"] == 0.5

    print("\n=== SCANNER BACKTEST VALIDATION: PASSED ===")


if __name__ == "__main__":
    main()
