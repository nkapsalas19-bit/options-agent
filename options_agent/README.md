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
| `fundamentals.py` | Earnings-date lookup and sector-ETF mapping (live-only enrichments, best-effort) |
| `scanner.py` | Combines technicals + relative strength + multi-timeframe confluence + news + earnings/sector into scored "callouts" (see below) |
| `scanner_backtest.py` | Walk-forward backtest of the scanner's technical scoring against real history — the evidence layer, see below |
| `market_scanner.py` | The scanner's run loop: sweeps the universe, writes `scanner_results.json`, emails high-confidence callouts |
| `alerts.py` | Gmail SMTP email alerts (prints to console instead if credentials aren't set) |
| `test_pipeline.py` | Validates the SPY/QQQ pipeline with synthetic data (see below for why) |
| `test_scanner.py` | Validates scanner scoring logic (technical + RS + MTF + news) with synthetic data + stubbed news/earnings |
| `test_scanner_backtest.py` | Validates the backtest's trade simulation and calibration math against constructed OHLC series |

## Market Scanner

`scanner.py` scans a ticker universe across two timeframes and produces
ranked callouts:

- **swing** (daily bars) → primarily suggests **buying/shorting shares**,
  plus a longer-dated option alternative (`config.SWING_TARGET_DTE_DAYS`).
- **intraday** (15-minute bars) → suggests short-dated **calls/puts**
  (`config.TARGET_DTE_DAYS`).

Each callout's **confidence score (0-100)** is fully explainable and split
into two tiers on purpose:

**Backtestable tier (up to 80 pts, all from OHLCV alone — see next section):**
- up to 30 pts: how many configured strategies fired the same direction
  (with the exact MA/RSI/Bollinger levels that triggered it)
- up to 10 pts: RSI confirmation
- up to 10 pts: volume vs. its 20-bar average
- up to 10 pts: alignment with the longer-term trend filter (see below)
- up to 10 pts: **multi-timeframe confluence** (intraday callouts only) —
  does the *daily* trend agree with the 15-minute signal, not just its own
  short-term trend?
- up to 10 pts: **relative strength vs. SPY** — has this ticker actually
  out/underperformed the market by a meaningful margin over the last 60
  days, or is the signal happening in a name nobody's trading?

**Live-only tier (not backtested — no free historical data for either):**
- up to ±20 pts: **news sentiment** (VADER over recent yfinance headlines)
- up to +5 pts: **sector confirmation** — is the whole sector ETF (e.g. XLK
  for tech) moving the same direction, or is this one name isolated?
- up to −10 pts: **earnings-date risk** — flags/penalizes an option
  suggestion whose DTE window contains an earnings print (IV crush risk)

Callouts scoring below `config.MIN_CONFIDENCE_SCORE` are discarded entirely.
`reasons` on every callout is a list of specific, numeric statements (actual
prices, RSI values, volume ratios, relative-strength percentages, headline
text) — there is no opaque model in the loop, and every claim is checkable
against the data that produced it.

### Trend filter (the main accuracy lever)

A signal that fights the longer-term trend is a well-documented source of
losing "textbook" setups. So on the **swing** timeframe, a signal is compared
against the `config.TREND_FILTER_PERIOD`-bar (default 50) SMA, and a
**counter-trend signal is discarded outright**, not just scored lower — e.g.
a bearish crossover while price is still well above its 50-day average never
becomes a callout. On **intraday**, the same check against a shorter SMA
(`config.INTRADAY_TREND_FILTER_PERIOD`, default 20) is a bonus/warning only,
since a short-term mean-reversion trade against the intraday trend is a
legitimate setup, unlike a multi-day counter-trend swing position.

### Exit plan on every callout

Every callout ships an `exit_plan`, not just an entry idea:
- **Shares**: stop-loss and profit-target computed from **ATR** (Average
  True Range, `config.ATR_PERIOD`) — `config.ATR_STOP_MULT` /
  `config.ATR_TARGET_MULT` — so the levels scale with each ticker's actual
  recent volatility instead of one flat percentage applied to every name
  (a fixed 2% stop means something very different on a low-vol utility than
  a high-vol momentum name). Reward:risk ratio is reported alongside.
- **Option alt**: stop/target as a percent of the option premium
  (`config.PROFIT_TARGET_PCT` / `config.STOP_LOSS_PCT`), since theta decay
  makes an underlying-price-based stop unreliable for a short-dated
  contract's actual P&L.
- **Time-stop**: an explicit "close/re-evaluate by X" rule
  (`config.SWING_MAX_HOLD_DAYS` for shares, `config.INTRADAY_MAX_HOLD_BARS`
  for the option), because a setup that goes nowhere for that long has
  usually stopped being the thesis that triggered it.
- **Invalidation rule**: an exit condition tied to the technical trigger
  itself (e.g. "the fast/slow MA re-crosses the other way") — an exit signal
  that can fire *before* the stop-loss price is even touched.

### Backtest evidence — the trust layer

A confidence score is only worth trusting if someone checked whether it
actually correlates with a good outcome on real data. That's what
`scanner_backtest.py` does: it replays `scanner.backtestable_score` —
**the exact same function the live scanner calls**, not a separate
reimplementation that could quietly drift out of sync — bar-by-bar across
each ticker's history, simulates the ATR-based exit plan forward from every
signal, and reports:

- **Overall stats**: trade count, win rate, profit factor, expectancy (in R,
  i.e. multiples of risked capital), and a max-drawdown proxy.
- **A calibration table**: trades bucketed by their technical score, showing
  the win rate *within each bucket*. This is the actual evidence for "does a
  higher score mean anything" — published as-is, including if a bucket
  doesn't calibrate cleanly, rather than only surfacing the backtest when it
  looks good.

No lookahead: every indicator (SMA/RSI/Bollinger/ATR) is a `.rolling()`
computation, causal by construction, so evaluating it at any historical bar
using the full precomputed series is mathematically identical to
recomputing it fresh on data truncated at that bar. Trades don't overlap on
the same ticker/timeframe, matching how the live scanner only holds one
position per name.

**What this does NOT prove**, on purpose, not by omission:
- News sentiment and earnings-date risk aren't included — yfinance has no
  historical news archive on the free tier, so there's no way to backtest
  what the sentiment score would have read on a given past date. A live
  confidence score and the backtest's technical score are related but not
  identical, and that gap is the honest cost of using free data sources.
- Intraday's multi-timeframe confluence bonus isn't backtested either
  (it needs the intraday and daily bars date-aligned bar-by-bar across
  history, which is out of scope for this pass) — intraday backtest scores
  have a lower ceiling than live intraday scores for that reason.
- No real bid-ask spread, slippage, commissions, or actual historical option
  prices (same limitation as `backtest.py` — see below).
- **Past performance on historical data is not a guarantee of anything
  live.** A calibration table that looks clean on 2-3 years of data can
  still fail on the next 2-3 years; markets change regimes.

Run it and read the calibration table before trusting any score this thing
shows you:

```bash
python scanner_backtest.py
```

This writes `scanner_backtest_results.json`, which the dashboard's
"Backtest Evidence" panel (`/api/backtest`) displays alongside the live
scanner output.

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
