# SPY/QQQ Options Pattern Agent + Market Scanner

A rule-based agent that detects chart patterns on SPY and QQQ, backtests
several candidate strategies, and trades the winner via calls/puts. Paper
trading is fully wired up; live execution is intentionally left as a stub
you complete yourself. It also includes a broader **market scanner**
(`scanner.py` / `market_scanner.py`) that combines technicals with free news
sentiment across a wider ticker universe — see "Market Scanner" below.

**No trading system, this one included, can honestly promise a 97%+ win
rate.** Every score this scanner produces is a transparent rule-based
confidence score (which indicators agreed, whether news sentiment lines up),
not a probability of profit. Treat every callout as a lead to research
further, not an instruction to execute.

## What's actually in here

| File | Purpose |
|---|---|
| `config.py` | Every tunable parameter — tickers, strategy params, risk/exit rules, mode, scanner settings |
| `data_fetcher.py` | Pulls OHLCV from yfinance (free, delayed ~15min, no historical options chains) |
| `indicators.py` | SMA, RSI, Bollinger Bands, opening range |
| `strategies.py` | Three candidate patterns: MA/RSI crossover, Bollinger squeeze breakout, opening range breakout (ORB) |
| `options_pricing.py` | Black-Scholes pricing + strike selection |
| `backtest.py` | Event-driven backtest engine, simulates options P&L bar-by-bar |
| `strategy_selector.py` | Runs all strategies × both tickers, ranks by risk-adjusted expectancy, picks winners |
| `broker.py` | `PaperBroker` (fully functional, simulated fills, local ledger) and `LiveBroker` (stub, raises until you implement it) |
| `live_agent.py` | The run loop for SPY/QQQ only: pull data → check signal → route to broker → manage exits |
| `universe.py` | Ticker universe for the scanner: live S&P 500 fetch (Wikipedia) or a curated fallback/watchlist |
| `news.py` | Free headline fetch (yfinance) + VADER sentiment scoring, no API key needed |
| `scanner.py` | Combines technicals + news sentiment into scored "callouts" across timeframes (see below) |
| `market_scanner.py` | The scanner's run loop: sweeps the universe, writes `scanner_results.json`, emails high-confidence callouts |
| `alerts.py` | Gmail SMTP email alerts (prints to console instead if credentials aren't set) |
| `test_pipeline.py` | Validates the SPY/QQQ pipeline with synthetic data (see below for why) |
| `test_scanner.py` | Validates scanner scoring logic with synthetic data + stubbed news |

## Market Scanner

`scanner.py` scans a ticker universe across two timeframes and produces
ranked callouts:

- **swing** (daily bars) → primarily suggests **buying/shorting shares**,
  plus a longer-dated option alternative (`config.SWING_TARGET_DTE_DAYS`).
- **intraday** (15-minute bars) → suggests short-dated **calls/puts**
  (`config.TARGET_DTE_DAYS`).

Each callout's **confidence score (0-100)** is fully explainable, built from:
- up to 60 pts of **technical agreement** — how many configured strategies
  fired the same direction, RSI confirmation, volume above its 20-bar average.
- up to ±40 pts from **news sentiment** (VADER over recent yfinance
  headlines) — a bonus if it agrees with the technical direction, a penalty
  if it conflicts, zero if no headlines were found.

Callouts scoring below `config.MIN_CONFIDENCE_SCORE` are discarded entirely.
`reasons` on every callout lists exactly which rules fired — there is no
opaque model in the loop.

### Honest limits on "real-time"

- **News isn't streaming.** yfinance's news endpoint has no push/websocket
  mechanism on the free tier — it reflects whatever's been indexed, often
  minutes behind the actual publish time. A true sub-second feed needs a
  paid provider (Polygon.io, Benzinga, Alpaca News, etc.) — swap `news.py`'s
  `fetch_recent_headlines()` for that provider's API if/when you get one.
- **Sentiment is general-purpose, not finance-tuned.** VADER can misjudge
  domain phrasing (e.g. "beats guidance" vs. "warns of headwinds").
- **A full universe sweep takes real time**, not a second. `market_scanner.py`
  prints each cycle's actual duration — set `config.SCAN_INTERVAL_SECONDS` to
  at least that, or scan fewer tickers via `SCANNER_UNIVERSE_MODE = "watchlist"`.

### Running the scanner

```bash
pip install -r requirements.txt
python market_scanner.py          # single sweep by default (see main_loop() to poll continuously)
```

This writes `scanner_results.json`, which the dashboard's new "Market
Scanner" panel (`webapp/app.py`'s `/api/scanner` route) reads and displays —
run the scanner and the dashboard as two separate processes.

### Email alerts

Set these environment variables to get emailed when a callout crosses
`config.ALERT_MIN_CONFIDENCE` (deduped to once per ticker/timeframe/direction
per day):

- `GMAIL_ADDRESS` — the Gmail account to send from
- `GMAIL_APP_PASSWORD` — a 16-character [App Password](https://myaccount.google.com/apppasswords)
  (not your normal Gmail password; requires 2-Step Verification on the account)
- `ALERT_TO_EMAIL` — recipient address (defaults to `GMAIL_ADDRESS` if unset)

Without these set, `alerts.py` prints the alert to the console instead of
failing, so the scanner keeps running.

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
