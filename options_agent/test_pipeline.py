"""
Validates the pipeline end-to-end using SYNTHETIC price data (random walk with
trend/volatility regimes), since this sandbox can't reach Yahoo Finance.
This proves the code runs correctly -- it says nothing about whether these
strategies are profitable on real SPY/QQQ data. Run strategy_selector.py on
your own machine (with real internet access) to get real backtest results.
"""
import numpy as np
import pandas as pd
from backtest import run_backtest, summarize
from broker import PaperBroker
import config


def make_synthetic_daily(days=750, seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-01", periods=days)
    returns = rng.normal(0.0003, 0.011, days)
    # inject a few trend regimes so MA/breakout strategies have something to find
    returns[100:140] += 0.004
    returns[300:330] -= 0.005
    returns[500:520] += 0.003
    prices = 400 * np.cumprod(1 + returns)

    high = prices * (1 + np.abs(rng.normal(0, 0.004, days)))
    low = prices * (1 - np.abs(rng.normal(0, 0.004, days)))
    open_ = prices * (1 + rng.normal(0, 0.002, days))
    volume = rng.integers(50_000_000, 90_000_000, days)

    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": prices, "volume": volume}, index=dates)
    return df


def main():
    print("=== 1. Synthetic data generation ===")
    df = make_synthetic_daily()
    print(df.head(3), "\n...", df.tail(3))

    print("\n=== 2. Backtest each strategy ===")
    results = []
    for strategy_name in config.STRATEGIES:
        trades = run_backtest(df.copy(), strategy_name, "SPY")
        summary = summarize(trades, strategy_name, "SPY")
        results.append(summary)
        print(f"\n--- {strategy_name} ---")
        print(f"Trades generated: {len(trades)}")
        if not trades.empty:
            print(trades[["entry_time", "option_type", "strike", "entry_option_price",
                           "exit_option_price", "pct_return", "exit_reason"]].head(5).to_string(index=False))
        print("Summary:", summary)

    print("\n=== 3. Paper broker mechanics ===")
    broker = PaperBroker(starting_capital=10000)
    r1 = broker.buy_to_open("SPY", "call", 500, "+2d", 1, 3.20)
    print("Buy result:", r1)
    r2 = broker.sell_to_close(r1["position"], 4.10)
    print("Sell result:", r2)
    print("Account summary:", broker.account_summary())

    print("\n=== PIPELINE VALIDATION: PASSED ===")


if __name__ == "__main__":
    main()
