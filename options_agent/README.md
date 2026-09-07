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
| `challenge.py` | Goal-oriented paper-trading tracker — position sizing, ledger, auto-resolution (see "Trading Challenge" below) |
| `test_pipeline.py` | Validates the SPY/QQQ pipeline with synthetic data (see below for why) |
| `test_scanner.py` | Validates scanner scoring logic (technical + RS + MTF + news) with synthetic data + stubbed news/earnings |
| `test_scanner_backtest.py` | Validates the backtest's trade simulation and calibration math against constructed OHLC series |
| `test_challenge.py` | Validates challenge sizing, ledger math, and auto-resolution (target/stop/time-stop/option re-pricing) |

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
- **Option alt**: states the exact contract — call or put, strike,
  **expiration date** (not just a day count) — plus two things:
  1. A percent-of-premium stop/target (`config.PROFIT_TARGET_PCT` /
     `config.STOP_LOSS_PCT`), since theta decay makes an underlying-price-based
     stop unreliable for a short-dated contract's actual P&L.
  2. A **price thesis**: what the contract would actually be worth (re-priced
     with Black-Scholes) if the underlying reaches the *same* ATR target/stop
     used for the shares plan, a few days out. This ties the option's
     projected payoff to the same "what do we think the stock does" thesis
     instead of leaving it as an unrelated flat-percent rule — and it's
     usually a smaller number than you'd expect, because it also has to
     survive theta decay over that window, not just the price move.
- **Time-stop**: an explicit "close/re-evaluate by X" rule
  (`config.SWING_MAX_HOLD_DAYS` for shares, `config.INTRADAY_MAX_HOLD_BARS`
  for the option), because a setup that goes nowhere for that long has
  usually stopped being the thesis that triggered it.
- **Invalidation rule**: an exit condition tied to the technical trigger
  itself (e.g. "the fast/slow MA re-crosses the other way") — an exit signal
  that can fire *before* the stop-loss price is even touched.

### Risk score (1-10) — separate from confidence, on purpose

Every callout also carries a **risk score**, shown as a color-coded bar next
to the confidence bar (green/amber/red for LOW/MODERATE/HIGH). This is a
deliberately different question from confidence: confidence asks "how much
evidence supports this direction," risk asks "how much could this cost you
if you're wrong, or even if you're right but slow." A high-confidence
callout can still be high-risk — e.g. a volatile name with an imminent
earnings print.

Risk is computed separately for the shares leg and the option leg (an
option is never scored as *less* risky than the equivalent shares position),
from:
- **Volatility** — ATR as a percent of price (`config.RISK_ATR_MED_PCT` /
  `RISK_ATR_HIGH_PCT`)
- **Time exposure** — for options, how short-dated the contract is
  (`config.RISK_DTE_VERY_SHORT` / `RISK_DTE_SHORT` — theta/gamma risk
  compounds fast on a 2-day contract); for shares, the fact that there's no
  expiration but full point-for-point exposure
- **Implied volatility** — expensive premium and IV-crush exposure on a
  high-IV assumption (`config.RISK_IV_MED` / `RISK_IV_HIGH`)
- **Earnings proximity** — a print landing inside the expected hold window
  adds gap risk regardless of direction

Like the reasons list, every risk score comes with its own itemized
breakdown (visible in the dashboard's expanded "Risk" section) — nothing
about it is a black box either.

### See it on the chart, not just as text

Every callout has a **"📈 View Chart"** button that loads that exact
ticker and timeframe into the main chart at the top of the dashboard (daily
bars for swing, 15-minute bars for intraday) and draws the entry, target,
and stop directly on the candles as horizontal reference lines. The
technical reasoning becomes something you can actually look at — the
crossover or breakout the callout is about is right there on the chart —
instead of only being a bullet list of numbers.

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
shows you — click **"Run Backtest"** in the dashboard's "Backtest Evidence"
panel (see "One-click dashboard" below), or from a terminal:

```bash
python scanner_backtest.py
```

Either way it writes `scanner_backtest_results.json`, which the dashboard's
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
  at least that. `config.SCANNER_UNIVERSE_MODE` defaults to `"watchlist"`
  (the short list in `config.SCANNER_WATCHLIST`) rather than `"sp500"`
  (~500 tickers) for exactly this reason, with a second one on top: Yahoo
  Finance is known to rate-limit or block requests from cloud-hosting IP
  ranges (AWS/GCP/Render/etc.) much more aggressively than from a home
  network, so a full S&P 500 sweep is both slower AND more likely to fail
  outright when this is deployed rather than run locally. Switch to
  `"sp500"` once you've confirmed watchlist mode is reliable in your actual
  hosting environment.

### Changing which tickers get scanned

**On the fly, no redeploy needed:** click **"🔧 Customize Tickers"** in the
Market Scanner panel. It opens a picker with 30 checkboxes
(`config.TOP_30_MOST_TRADED` — a curated set of liquid, commonly
heavily-traded US stocks/ETFs, **not** a live volume ranking, since there's
no real-time volume feed behind this) plus a text box for any tickers not
on that list. Check/type what you want, click **"Scan Selected"**, and it
scans exactly that list right away — a one-off scan that doesn't touch your
permanent configuration.

**To make a selection your permanent default**, copy the list shown at the
bottom of the picker (there's a "Copy" button) into an environment variable
named `SCANNER_WATCHLIST` — on Render, that's Settings → Environment → add
a variable, no different from setting `DASHBOARD_PASSWORD`. Restart/redeploy
to pick it up. Leave it unset to keep the default 10-ticker list in
`config.py`.

The dashboard also always shows exactly what's configured, right in the
"Market Scanner" panel — a line reading "Configured to scan (watchlist
mode): ..." before you've run a scan, and "Last scanned: ..." with the
actual list used after one completes (whether that came from the default
config or a picker-driven one-off scan). And the main chart at the top
isn't limited to SPY/QQQ either — there's a text box next to those buttons
where you can type any ticker (e.g. `AAPL`) to load its chart directly,
independent of the scanner.

### Getting more detail on a callout

Click anywhere on a callout's row in the Market Scanner panel to expand
it — this reveals a plain-English one-line summary of why it fired, the
full itemized reasoning list (every rule that contributed to the score,
with the actual numbers), the risk breakdown for both the shares and
option legs, and the complete exit plan. Click the row again to collapse
it. The "📈 View Chart" button inside the expanded view loads that exact
ticker/timeframe into the main chart with entry/target/stop drawn on it.

### One-click dashboard — no terminal required after setup

Once the dashboard is running (`python webapp/app.py`, visit
`http://localhost:5000`), everything else is a button click:

- **"Scan Now"** (Market Scanner panel) triggers a full sweep of the
  configured universe in the background and refreshes the callout list when
  it's done. The button shows a live timer and stays disabled while it
  works. The default watchlist (10 tickers) should finish in well under a
  minute; switching to `"sp500"` mode makes this a genuinely slow operation
  on free data (see "Honest limits" above), so the button is built to make
  that wait visible rather than pretend it's instant.
- **"Run Backtest"** (Backtest Evidence panel) triggers
  `scanner_backtest.py` the same way — background job, live timer,
  auto-refresh on completion.
- Clicking either button while its job is still running just keeps polling
  the same job instead of starting a second overlapping one.
- If a job fails (e.g. no network), the button turns red and shows the
  error on hover instead of failing silently.

You still never *need* a terminal for either of these day-to-day, but the
underlying scripts still run standalone too, useful for scheduling (cron,
Task Scheduler) or running headless without the dashboard open:

```bash
pip install -r requirements.txt
python market_scanner.py          # single sweep by default (see main_loop() to poll continuously)
python scanner_backtest.py
```

Both write to the same JSON files the dashboard's buttons produce
(`scanner_results.json`, `scanner_backtest_results.json`), so a scheduled
script and the on-demand buttons interchange freely — whichever ran most
recently is what the dashboard shows.

### Fully automatic scanning — no clicking required

The dashboard scans itself. A background thread starts when the web
service boots and re-scans every `config.SCAN_INTERVAL_SECONDS` (default
5 minutes) for as long as the process stays alive — you'll see a green
**"🔄 Auto-scanning every ~5 min..."** line in the Market Scanner panel
confirming it's on. It shares the same job lock as the "Scan Now" button,
so the two never collide or double up. Set the environment variable
`AUTO_SCAN_ENABLED=false` to turn it off (e.g. if you ever run more than
one worker process, since each would otherwise run its own loop — Render's
free tier defaults to a single worker, so this isn't a concern there).

**The catch, and it's a real one**: Render's free tier spins the whole
service down after ~15 minutes with no HTTP traffic, which stops this
background thread along with everything else. Auto-scanning only runs
while something is keeping the service awake — which happens naturally
if you (or anyone) has the dashboard open, since the page's own periodic
requests count as traffic.

**To keep it scanning even when nobody's looking at the page**, point a
free external scheduler at a special endpoint that both wakes the service
up and triggers a scan:

1. Set an environment variable `CRON_SECRET` to any long random string
   (same place as `DASHBOARD_PASSWORD`). This endpoint refuses every
   request until this is set, so it's harmless to leave configured.
2. Sign up for a free scheduler like [cron-job.org](https://cron-job.org)
   (no card required) and create a job that sends a GET request every
   5-10 minutes to:
   ```
   https://your-app-name.onrender.com/api/cron/scan?token=YOUR_CRON_SECRET
   ```
3. That's it — every ping wakes the service (if it was asleep) and starts
   a scan (if one isn't already running).

This endpoint deliberately doesn't use the dashboard password — an
external scheduler can't do a browser login — it's protected by the
`CRON_SECRET` token instead, checked with a constant-time comparison.
Anyone who obtained that token could trigger extra scans (wasted API
calls, not a security or financial risk, since this is still paper
trading), so treat it with the same care as any other secret, but it's
not something to lose sleep over.

### Accessing the dashboard from your phone or another computer

`python webapp/app.py` only serves `http://localhost:5000` — reachable
from the same machine it's running on, nothing else. Two ways to get past
that, from quick-and-free to always-on:

**1. Same WiFi, right now (free, a few minutes):**
1. Find your computer's local IP address:
   - Mac: System Settings → Wi-Fi → Details → IP Address
   - Windows: `ipconfig` in Command Prompt → "IPv4 Address"
   - Linux: `hostname -I`
   - It'll look like `192.168.1.23` or `10.0.0.15`.
2. Keep `python webapp/app.py` running (it already listens on `0.0.0.0`,
   meaning "every network interface," not just localhost — no code change
   needed).
3. On your phone (connected to the **same WiFi network**), visit
   `http://<that-ip>:5000` — e.g. `http://192.168.1.23:5000`.
4. This only works while your computer is on, awake, and running the app,
   and only from devices on the same network (not out on cellular data away
   from home). If nothing loads, your computer's firewall may be blocking
   incoming connections on port 5000 — allow it for your local network.

**2. A real URL, reachable from anywhere, anytime (free tier available):**
This repo already has everything needed for this — see
[`webapp/DEPLOY.md`](webapp/DEPLOY.md) for the exact click-by-click steps
to deploy to [Render](https://render.com) (free tier, no credit card).
Once deployed you get a permanent URL like
`https://your-app-name.onrender.com` that works from your phone, a friend's
computer, anywhere — password-gated by `DASHBOARD_PASSWORD`. The free
tier sleeps after 15 minutes of no traffic and takes 30-60 seconds to wake
back up on the next visit; fine for personal use.

One thing to set up either way before relying on it: **change
`DASHBOARD_PASSWORD` from its default** (`changeme123`) via the environment
variable described in `webapp/app.py` — anyone who finds the URL can see
your callouts and trigger scans/backtests otherwise.

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

## Trading Challenge

A goal-oriented paper-trading tracker layered on top of the scanner
(`challenge.py`), for questions like "if I have $1,000 and want to reach
$2,000 in the next 60 days, what would this suggest, and how would it
actually have gone." Set it up in the dashboard's "Trading Challenge"
panel:

- **Start date / end date** — your timeframe.
- **Starting budget** — how much paper capital you're starting with.
- **Goal amount** — what you're trying to reach by the end date.
- **Instrument preference** — shares only, options only, or "both" (defers
  to whatever each callout's own timeframe already suggests as primary).
- **Risk per trade (%)** — the max percent of your *current* balance any
  single suggested position is allowed to risk (default 3%, capped between
  0.5% and 25% to guard against a fat-fingered input, not as trading
  advice about what's "safe").

### How recommendations get sized

Every scanner callout that matches your instrument preference gets a
**"Suggested: N shares/contracts... — cost $X, risking $Y"** box with an
"Add to Challenge" button, computed from your *current* cash balance (not
your original starting budget — sizing shrinks automatically as the
balance changes) and the risk-per-trade cap, using the exact same
ATR-based stop distance already shown in that callout's exit plan. If
even one unit would risk more than your cap allows, or cost more than you
have, it's marked as not fitting rather than sized down to something the
scoring doesn't actually support.

### What happens after you add a trade

It goes into a ledger (`challenge_trades.json`) as an open position, and
**resolves automatically** — every time you run a scan (via the "Scan Now"
button or a scheduled `market_scanner.py`), `challenge.check_open_positions()`
fetches fresh price data for each open position's ticker and checks whether
it's crossed its target, its stop, or exceeded its planned hold window
since entry:

- **Shares**: resolves against the underlying price directly.
- **Options**: resolves against the *underlying's* target/stop (the same
  levels used for the shares side of that callout), then re-prices the
  option with Black-Scholes at the actual exit date and underlying price —
  not the static premium target guessed at recommendation time, which
  would assume a specific number of days had passed that may not match
  reality.
- **Same-window ambiguity resolves to the stop**, not the target — the
  same conservative convention `scanner_backtest.py` uses, applied
  consistently rather than picking whichever outcome looks better.
- A position open longer than its planned hold window closes at the last
  available price with reason `"time_stop"`, so nothing lingers open
  forever skewing the numbers.

The panel shows equity, cash, realized P&L, a progress bar (your % of the
way to the goal vs. % of the timeframe elapsed — a quick "on pace or not"
read), and full open/closed position tables.

### What this is and isn't

- **It's still paper trading.** No real broker, no real fills, no real
  bid-ask spread — same approximations as the rest of this repo.
- **Open positions are marked at cost, not re-priced live**, so "Equity"
  moves in steps as trades close rather than continuously — a deliberate
  simplicity trade-off, not an attempt to show a live mark-to-market P&L.
- **A goal is not a probability.** Setting "$1,000 to $2,000 in 30 days"
  doesn't mean the system thinks that's likely — it sizes recommendations
  to fit that budget and risk cap, nothing more. Compare your actual pace
  against the Backtest Evidence panel's real expectancy per trade (see
  above) before assuming an aggressive goal is realistic on this edge.

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
