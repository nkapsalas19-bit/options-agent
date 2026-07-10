"""
Event-driven backtester. Walks bar-by-bar through historical underlying prices,
takes strategy signals, and simulates the resulting option position's P&L using
Black-Scholes (see options_pricing.py for why).

Reminder: this measures whether a PATTERN correlates with favorable underlying
moves large enough to overcome theta decay -- it is not a guarantee of live
fills, spread costs, or slippage.
"""
import pandas as pd
import numpy as np
from options_pricing import bs_price, select_strike
import config


def run_backtest(df, strategy_name, ticker):
    params = config.STRATEGIES[strategy_name]
    from strategies import STRATEGY_FUNCS
    df = STRATEGY_FUNCS[strategy_name](df, params)

    iv = config.DEFAULT_IV.get(ticker, 0.16)
    rate = config.RISK_FREE_RATE
    dte = config.TARGET_DTE_DAYS

    trades = []
    position = None

    for i in range(len(df)):
        row = df.iloc[i]
        signal = row["signal"]

        if position is None and signal != 0:
            option_type = "call" if signal == 1 else "put"
            strike = select_strike(row["close"], config.STRIKE_OFFSET_PCT, option_type)
            entry_price = bs_price(row["close"], strike, dte, iv, rate, option_type)
            position = {
                "entry_idx": i,
                "entry_time": df.index[i],
                "entry_spot": row["close"],
                "option_type": option_type,
                "strike": strike,
                "entry_option_price": entry_price,
                "dte_at_entry": dte,
            }
            continue

        if position is not None:
            bars_held = i - position["entry_idx"]
            remaining_dte = max(position["dte_at_entry"] - bars_held * _bar_to_day_fraction(df), 0.01)
            current_price = bs_price(row["close"], position["strike"], remaining_dte, iv, rate,
                                      position["option_type"])
            pct_change = (current_price - position["entry_option_price"]) / position["entry_option_price"]

            exit_reason = None
            if pct_change >= config.PROFIT_TARGET_PCT:
                exit_reason = "profit_target"
            elif pct_change <= -config.STOP_LOSS_PCT:
                exit_reason = "stop_loss"
            elif bars_held >= config.MAX_HOLD_BARS:
                exit_reason = "time_exit"
            elif remaining_dte <= 0.05:
                exit_reason = "expiry"

            if exit_reason:
                trades.append({
                    **position,
                    "exit_time": df.index[i],
                    "exit_spot": row["close"],
                    "exit_option_price": current_price,
                    "pct_return": pct_change,
                    "exit_reason": exit_reason,
                })
                position = None

    return pd.DataFrame(trades)


def _bar_to_day_fraction(df):
    """Estimate how much of a trading day one bar represents, for decay purposes."""
    if len(df) < 2:
        return 1.0
    freq = (df.index[1] - df.index[0]).total_seconds()
    trading_day_seconds = 6.5 * 3600  # 9:30-16:00 ET
    return max(freq / trading_day_seconds, 1 / 390)  # floor at 1 min-equivalent


def summarize(trades_df, strategy_name, ticker):
    if trades_df.empty or len(trades_df) < config.BACKTEST_METRICS_MIN_TRADES:
        return {
            "strategy": strategy_name, "ticker": ticker,
            "trades": len(trades_df), "note": "below min trade threshold, stats unreliable",
        }

    wins = trades_df[trades_df["pct_return"] > 0]
    losses = trades_df[trades_df["pct_return"] <= 0]
    win_rate = len(wins) / len(trades_df)
    avg_win = wins["pct_return"].mean() if not wins.empty else 0
    avg_loss = losses["pct_return"].mean() if not losses.empty else 0
    profit_factor = (wins["pct_return"].sum() / abs(losses["pct_return"].sum())
                      if losses["pct_return"].sum() != 0 else np.inf)
    expectancy = trades_df["pct_return"].mean()

    cum_returns = (1 + trades_df["pct_return"]).cumprod()
    running_max = cum_returns.cummax()
    drawdown = (cum_returns - running_max) / running_max
    max_drawdown = drawdown.min()

    return {
        "strategy": strategy_name,
        "ticker": ticker,
        "trades": len(trades_df),
        "win_rate": round(win_rate, 3),
        "avg_win_pct": round(avg_win, 3),
        "avg_loss_pct": round(avg_loss, 3),
        "profit_factor": round(profit_factor, 2) if np.isfinite(profit_factor) else "inf",
        "expectancy_pct": round(expectancy, 4),
        "max_drawdown_pct": round(max_drawdown, 3),
    }
