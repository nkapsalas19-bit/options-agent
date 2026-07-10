"""
Runs every candidate strategy against every ticker, ranks by a composite score,
and picks the winner(s) to actually trade live. This is what "master a few
patterns, decided by backtest results" means in code.
"""
import pandas as pd
import config
from data_fetcher import fetch_daily
from backtest import run_backtest, summarize


def evaluate_all():
    results = []
    raw_trades = {}

    for ticker in config.TICKERS:
        df = fetch_daily(ticker, period=config.BACKTEST_PERIOD)
        for strategy_name in config.STRATEGIES:
            trades = run_backtest(df, strategy_name, ticker)
            summary = summarize(trades, strategy_name, ticker)
            results.append(summary)
            raw_trades[(ticker, strategy_name)] = trades

    results_df = pd.DataFrame(results)
    return results_df, raw_trades


def rank_strategies(results_df):
    """Composite score: expectancy weighted by trade count confidence, penalized by drawdown."""
    valid = results_df[results_df.get("expectancy_pct").notna()].copy() if "expectancy_pct" in results_df else pd.DataFrame()
    if valid.empty:
        return results_df  # nothing had enough trades

    valid["score"] = (
        valid["expectancy_pct"] * valid["win_rate"]
        - valid["max_drawdown_pct"].abs() * 0.5
    )
    return valid.sort_values("score", ascending=False)


def select_best_per_ticker(ranked_df):
    """Returns a dict: {ticker: best_strategy_name}"""
    best = {}
    for ticker in config.TICKERS:
        subset = ranked_df[ranked_df["ticker"] == ticker]
        if not subset.empty:
            best[ticker] = subset.iloc[0]["strategy"]
    return best


if __name__ == "__main__":
    results_df, _ = evaluate_all()
    print("\n=== Raw backtest results ===")
    print(results_df.to_string(index=False))

    ranked = rank_strategies(results_df)
    print("\n=== Ranked (best first) ===")
    print(ranked.to_string(index=False) if not ranked.empty else "No strategy met the min-trade threshold.")

    if not ranked.empty:
        best = select_best_per_ticker(ranked)
        print("\n=== Selected strategy per ticker ===")
        print(best)
