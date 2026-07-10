# SPY/QQQ Options Pattern Agent

A rule-based agent that detects chart patterns on SPY and QQQ, backtests
several candidate strategies, and trades the winner via calls/puts. Paper
trading is fully wired up; live execution is intentionally left as a stub
you complete yourself.

## What's actually in here

| File | Purpose |
|---|---|
| `config.py` | Every tunable parameter — tickers, strategy params, risk/exit rules, mode |
| `data_fetcher.py` | Pulls OHLCV from yfinance (free, delayed ~15min, no historical options chains) |
| `indicators.py` | SMA, RSI, Bollinger Bands, opening range |
| `strategies.py` | Three candidate patterns: MA/RSI crossover, Bollinger squeeze breakout, opening range breakout (ORB) |
| `options_pricing.py` | Black-Scholes pricing + strike selection |
| `backtest.py` | Event-driven backtest engine, simulates options P&L bar-by-bar |
| `strategy_selector.py` | Runs all strategies × both tickers, ranks by risk-adjusted expectancy, picks winners |
| `broker.py` | `PaperBroker` (fully functional, simulated fills, local ledger) and `LiveBroker` (stub, raises until you implement it) |
| `live_agent.py` | The run loop: pull data → check signal → route to broker → manage exits |
| `test_pipeline.py` | Validates the whole pipeline with synthetic data (see below for why) |

## Critical limitation: backtest realism

**yfinance does not provide historical options chains** — only a snapshot of
today's. So there's no free way to backtest "what would this SPY call have
actually traded for on March 3, 2024." The workaround here is Black-Scholes:
take the real historical SPY/QQQ price, plug in a strike/DTE/assumed IV, and
compute a theoretical price.

This is a legitimate and commonly used approximation, but it's still an
approximation. It does **not** capture:
- Real bid-ask spreads (can be 5-15%+ of an option's value for 0-2 DTE)
- Volatility skew/smile (OTM puts and calls don't share one flat IV in reality)
- IV expansion/crush around specific events (Fed days, earnings-adjacent moves)
- Slippage and partial fills

Treat backtest output as **"does this pattern correlate with underlying moves
large enough to plausibly beat theta,"** not "this is my expected live P&L."

## Why `test_pipeline.py` uses synthetic data

This sandbox's network egress doesn't reach Yahoo Finance, so I validated the
code logic with a synthetic random-walk price series instead of real SPY
data. **Run `strategy_selector.py` on your own machine** (with normal internet
access) to get real backtest numbers before trusting any strategy.

```bash
pip install yfinance pandas numpy scipy pandas-ta
python strategy_selector.py
```

That prints a ranked table and picks the best strategy per ticker. Take those
results, sanity-check the trade log isn't dominated by one lucky trade, and
only then move to the next step.

## Path to running it

1. **Backtest** — `python strategy_selector.py`. Look at trade count (need 20+
   to trust the stats — `config.BACKTEST_METRICS_MIN_TRADES`), win rate,
   profit factor, max drawdown. Note ORB needs intraday data — swap
   `fetch_daily` for `fetch_intraday` in `strategy_selector.py` if you want to
   test it properly, since it can't fire on daily bars.
2. **Wire the winner** — open `live_agent.py`, set `SELECTED_STRATEGY = {"SPY":
   "...", "QQQ": "..."}` based on step 1's output.
3. **Paper trade** — `config.MODE = "paper"` (default). Run
   `python live_agent.py` — single pass by default. For continuous polling,
   call `main_loop(poll_seconds=60, max_iterations=None)`. Watch
   `paper_ledger.json` and `open_positions.json` accumulate real (simulated)
   trade history against live market data for at least several weeks across
   different market conditions before considering real money.
4. **Live** — only after you're satisfied with paper results:
   - Open an account with a broker with an options trading API (Tradier and
     Alpaca both support this).
   - Get your own API credentials from them.
   - Implement `LiveBroker.buy_to_open` / `sell_to_close` in `broker.py`
     against that broker's actual documented endpoints (deliberately not
     pre-filled — check their current docs, since these change).
   - Flip `config.MODE = "live"`.

## Honest risk note

Short-dated (0-2 DTE) SPY/QQQ options are high-variance instruments — theta
decay is brutal and a pattern that looks good in backtest can still lose
money live due to spread costs alone. Position sizing
(`config.POSITION_SIZE_CONTRACTS`) and hard stop-losses are in the code, but
no amount of code removes the risk that this loses money, including all of
it, especially early on. This is a tool, not a guarantee.
