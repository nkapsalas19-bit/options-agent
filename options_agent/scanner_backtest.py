"""
Walk-forward backtest of the scanner's BACKTESTABLE score (strategy
agreement, RSI, volume, the trend filter, multi-timeframe confluence, and
relative strength -- see scanner.backtestable_score) against real historical
OHLCV. This is what makes the confidence score trustworthy instead of just
plausible-sounding: it reports the ACTUAL historical win rate, profit
factor, and a score-calibration table (does a higher score actually correlate
with a better outcome, on this data?), not a number anyone asserts.

What this does NOT validate, and why:
  - News sentiment: yfinance has no historical news archive on the free
    tier -- only "what's in the feed right now." There is no free way to ask
    "what would the sentiment score have been on 2023-04-12" for hundreds of
    tickers. It's a live-only enrichment in scanner.py; it is not included
    in the tech_score this backtest reports on.
  - Earnings-date risk / sector confirmation: same story -- live-only,
    excluded here.
  - Intraday multi-timeframe confluence: doing this properly needs the
    intraday bars and the daily bars date-aligned bar-by-bar across history,
    which is meaningfully more plumbing than this pass covers. Intraday
    backtests here run WITHOUT the daily-confluence bonus (tech_score is
    correspondingly lower-ceiling than live for that timeframe) -- see
    TIMEFRAME_MAX below.
  - Real trading frictions: bid-ask spread, slippage, commissions, and
    (for the option alt specifically) actual historical option prices --
    yfinance has no historical options chains, so this backtest, like the
    rest of this repo, validates the underlying price pattern, not a
    guaranteed option fill.

No lookahead: every indicator used here (SMA/RSI/Bollinger Bands/ATR) is a
pandas .rolling() computation, which by construction only uses data up to
and including the row it's evaluated at. Computing them ONCE over the full
historical series and reading .iloc[idx] is mathematically identical to
recomputing them fresh on a truncated df ending at idx -- so this loop is
O(n) per ticker, not O(n^2), without sacrificing correctness. Trades don't
overlap on the same ticker/timeframe: a new entry is skipped while a
simulated position is still open, matching how the live scanner would only
hold one position per name.

Run: python scanner_backtest.py
"""
import json
import os

import numpy as np
import pandas as pd

import config
from data_fetcher import fetch_daily, fetch_intraday
from strategies import STRATEGY_FUNCS
from indicators import atr as atr_indicator
from scanner import backtestable_score, relative_strength_excess, TIMEFRAME_STRATEGIES

TIMEFRAME_MAX = {
    "swing": config.BACKTESTABLE_SCORE_MAX,                                    # includes RS, no MTF (self-timeframe)
    "intraday": config.TECH_SCORE_MAX + config.RS_BONUS,                       # no MTF bonus available here (see docstring)
}

RESULTS_PATH = os.path.join(os.path.dirname(__file__), "scanner_backtest_results.json")


def _simulate_trade(df, entry_idx, direction, atr_val, max_hold_bars):
    """Walks forward from entry_idx+1, bar by bar, checking whether the
    ATR-based stop or target hits first. A same-bar tie (both stop and target
    touched within one bar's high/low range) resolves to the stop -- the
    standard conservative assumption in OHLC-only backtesting, since the
    actual intra-bar path isn't known."""
    entry_price = df["close"].iloc[entry_idx]
    sign = 1 if direction == 1 else -1
    stop = entry_price - sign * config.ATR_STOP_MULT * atr_val
    target = entry_price + sign * config.ATR_TARGET_MULT * atr_val
    risk = abs(entry_price - stop)

    last_idx = min(entry_idx + max_hold_bars, len(df) - 1)
    for i in range(entry_idx + 1, last_idx + 1):
        bar = df.iloc[i]
        hit_stop = bar["low"] <= stop if direction == 1 else bar["high"] >= stop
        hit_target = bar["high"] >= target if direction == 1 else bar["low"] <= target
        if hit_stop:
            return {"exit_idx": i, "exit_price": float(stop), "outcome": "stop",
                    "r_multiple": -1.0}
        if hit_target:
            return {"exit_idx": i, "exit_price": float(target), "outcome": "target",
                    "r_multiple": config.ATR_TARGET_MULT / config.ATR_STOP_MULT}

    exit_price = df["close"].iloc[last_idx]
    exit_r = sign * (exit_price - entry_price) / risk if risk > 0 else 0.0
    return {"exit_idx": last_idx, "exit_price": float(exit_price), "outcome": "time_stop",
            "r_multiple": float(exit_r)}


def backtest_ticker(ticker, timeframe_name, period=None, benchmark_df=None):
    """Returns a list of simulated trades for one ticker/timeframe."""
    tf_cfg = TIMEFRAME_STRATEGIES[timeframe_name]
    is_daily = tf_cfg["interval"] == "1d"
    period = period or (config.BACKTEST_PERIOD if is_daily else tf_cfg["period"])  # 60d cap on free intraday history

    try:
        df = fetch_daily(ticker, period=period) if is_daily else fetch_intraday(ticker, interval=tf_cfg["interval"], period=period)
    except Exception as e:
        print(f"[backtest] {ticker}/{timeframe_name} fetch failed: {e}")
        return []

    warmup = max(config.TREND_FILTER_PERIOD, config.RS_LOOKBACK_DAYS, 60) + 5
    if len(df) < warmup + 10:
        return []

    precomputed = {name: STRATEGY_FUNCS[name](df, config.STRATEGIES[name]) for name in tf_cfg["strategies"]}
    atr_series = atr_indicator(df, config.ATR_PERIOD)
    max_hold = config.SWING_MAX_HOLD_DAYS if timeframe_name == "swing" else config.INTRADAY_MAX_HOLD_BARS

    trades = []
    next_allowed_idx = warmup

    for idx in range(warmup, len(df) - 1):
        if idx < next_allowed_idx:
            continue

        rs_value = relative_strength_excess(df, benchmark_df, idx=idx) if (is_daily and benchmark_df is not None) else None

        direction, score, reasons, discarded = backtestable_score(
            df, tf_cfg["strategies"],
            trend_filter_period=tf_cfg["trend_filter_period"],
            trend_filter_hard=tf_cfg["trend_filter_hard"],
            idx=idx, precomputed=precomputed,
            daily_df=None,  # see module docstring: intraday MTF confluence isn't backtested here
            rs_value=rs_value,
        )
        if direction == 0 or discarded:
            continue

        atr_val = atr_series.iloc[idx]
        if np.isnan(atr_val) or atr_val <= 0:
            continue

        outcome = _simulate_trade(df, idx, direction, atr_val, max_hold)
        trades.append({
            "ticker": ticker, "timeframe": timeframe_name,
            "entry_idx": int(idx), "entry_time": str(df.index[idx]),
            "direction": "BULLISH" if direction == 1 else "BEARISH",
            "tech_score": round(score, 1),
            **outcome,
        })
        next_allowed_idx = outcome["exit_idx"] + 1  # no overlapping positions on the same ticker/timeframe

    return trades


def aggregate_results(trades):
    if not trades:
        return {"trades": 0, "note": "no trades generated"}

    df = pd.DataFrame(trades)
    wins = df[df["r_multiple"] > 0]
    losses = df[df["r_multiple"] <= 0]
    win_rate = len(wins) / len(df)
    loss_sum = losses["r_multiple"].sum()
    profit_factor = wins["r_multiple"].sum() / abs(loss_sum) if loss_sum != 0 else float("inf")
    expectancy_r = df["r_multiple"].mean()

    equity = (1 + df["r_multiple"] * 0.01).cumprod()  # rough proxy: treats each R as 1% of account risked
    running_max = equity.cummax()
    max_dd = ((equity - running_max) / running_max).min()

    return {
        "trades": len(df),
        "win_rate": round(win_rate, 3),
        "profit_factor": round(profit_factor, 2) if np.isfinite(profit_factor) else "inf",
        "expectancy_r": round(float(expectancy_r), 3),
        "max_drawdown_proxy": round(float(max_dd), 3),
        "outcome_breakdown": df["outcome"].value_counts().to_dict(),
    }


def calibration_table(trades, timeframe_name, bucket_size=10):
    """Does a higher score actually correlate with a better outcome on this
    data? This is the evidence a claimed confidence score needs -- report it
    plainly, including if it DOESN'T calibrate well, rather than only
    publishing the number when it looks good."""
    if not trades:
        return []
    df = pd.DataFrame(trades)
    score_max = TIMEFRAME_MAX.get(timeframe_name, df["tech_score"].max())
    df["bucket_low"] = (df["tech_score"] // bucket_size * bucket_size).astype(int)

    rows = []
    for bucket_low, group in df.groupby("bucket_low"):
        wins = group[group["r_multiple"] > 0]
        rows.append({
            "score_bucket": f"{bucket_low}-{min(bucket_low + bucket_size - 1, int(score_max))}",
            "trades": len(group),
            "win_rate": round(len(wins) / len(group), 3),
            "avg_r": round(group["r_multiple"].mean(), 3),
        })
    return sorted(rows, key=lambda r: r["score_bucket"])


def run_full_backtest(tickers, timeframe_names=None):
    timeframe_names = timeframe_names or ["swing"]

    try:
        benchmark_df = fetch_daily(config.RS_BENCHMARK, period=config.BACKTEST_PERIOD)
    except Exception as e:
        print(f"[backtest] couldn't fetch relative-strength benchmark {config.RS_BENCHMARK}: {e}")
        benchmark_df = None

    all_trades = []
    for ticker in tickers:
        for tf in timeframe_names:
            all_trades.extend(backtest_ticker(ticker, tf, benchmark_df=benchmark_df if tf == "swing" else None))

    report = {}
    for tf in timeframe_names:
        tf_trades = [t for t in all_trades if t["timeframe"] == tf]
        overall = aggregate_results(tf_trades)
        if 0 < overall.get("trades", 0) < config.BACKTEST_METRICS_MIN_TRADES:
            overall["note"] = f"below {config.BACKTEST_METRICS_MIN_TRADES}-trade threshold, stats unreliable"
        report[tf] = {
            "overall": overall,
            "calibration": calibration_table(tf_trades, tf),
            "score_ceiling_this_timeframe": TIMEFRAME_MAX.get(tf),
        }

    return report, all_trades


if __name__ == "__main__":
    from universe import get_scan_universe

    tickers = get_scan_universe()
    print(f"Backtesting {len(tickers)} tickers over {config.BACKTEST_PERIOD} of daily history "
          f"(swing timeframe only by default -- intraday history is capped at ~60 days by yfinance's "
          f"free tier, too short a window to trust the resulting stats)...")

    report, trades = run_full_backtest(tickers, timeframe_names=["swing"])

    print(json.dumps(report, indent=2, default=str))
    with open(RESULTS_PATH, "w") as f:
        json.dump({"report": report, "generated_at": pd.Timestamp.now().isoformat(timespec="seconds")}, f, indent=2, default=str)
    print(f"\nWrote {RESULTS_PATH}")
