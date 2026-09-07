"""
Central config for the SPY/QQQ options pattern-trading agent.
Every tunable knob lives here so you're not hunting through modules.
"""

# ---- Universe ----
TICKERS = ["SPY", "QQQ"]

# ---- Data ----
DATA_INTERVAL = "5m"       # intraday bar size for signal generation
DATA_PERIOD = "60d"        # yfinance max lookback for 5m bars is ~60 days
BACKTEST_INTERVAL = "1d"   # daily bars for longer backtest history
BACKTEST_PERIOD = "3y"

# ---- Options pricing assumptions (Black-Scholes, since yfinance has no historical chains) ----
RISK_FREE_RATE = 0.045          # approx T-bill rate, update periodically
DEFAULT_IV = {"SPY": 0.14, "QQQ": 0.18}   # rough baseline IV, override with live VIX/VXN if available
TARGET_DTE_DAYS = 2             # days to expiration targeted at entry (short-dated)
STRIKE_OFFSET_PCT = 0.0         # 0 = ATM, positive = OTM by that % of spot

# ---- Strategy parameters (candidates the backtester will evaluate) ----
STRATEGIES = {
    "ma_rsi": {
        "fast_ma": 9,
        "slow_ma": 21,
        "rsi_period": 14,
        "rsi_oversold": 35,
        "rsi_overbought": 65,
    },
    "bb_squeeze_breakout": {
        "bb_period": 20,
        "bb_std": 2.0,
        "squeeze_lookback": 60,   # bars to look back for "is this a squeeze" percentile
        "squeeze_percentile": 20, # bandwidth must be in bottom 20% of lookback to qualify
    },
    "orb": {
        "range_minutes": 15,      # opening range window
        "min_volume_mult": 1.2,   # breakout volume vs opening range avg volume
    },
}

# ---- Risk / exit management ----
PROFIT_TARGET_PCT = 0.50   # close at +50% option value
STOP_LOSS_PCT = 0.35       # close at -35% option value
MAX_HOLD_BARS = 12         # time-based exit (in units of DATA_INTERVAL bars)
MAX_CONCURRENT_POSITIONS = 1
POSITION_SIZE_CONTRACTS = 1

# ---- Execution ----
MODE = "paper"              # "paper" or "live" -- live requires broker credentials, see broker.py
STARTING_CAPITAL = 10000.00

# ---- Backtest evaluation ----
BACKTEST_METRICS_MIN_TRADES = 20   # don't trust a strategy's stats below this trade count

# ---- Market scanner (news + technicals across a ticker universe, multiple timeframes) ----
# Honest limitation: this polls free data/news sources on SCAN_INTERVAL_SECONDS. Neither
# yfinance nor free news endpoints push true per-second updates -- a full sweep of the
# configured universe takes real wall-clock time (market_scanner.py prints it each cycle).
# Set SCAN_INTERVAL_SECONDS to at least that duration, or switch to a smaller watchlist.
#
# Defaults to "watchlist" (a short, fast, reliable list) rather than "sp500" (~500 tickers)
# on purpose: a full S&P 500 sweep means ~500 sequential calls to Yahoo Finance, which is
# both slow on constrained hosting (like a free-tier cloud instance) AND a well-known way to
# get rate-limited or blocked outright -- Yahoo's anti-scraping measures are especially
# aggressive toward requests coming from cloud-provider IP ranges (AWS/GCP/Render/etc.),
# much more so than from a home network. Switch to "sp500" once you've confirmed the
# watchlist mode works reliably in your actual environment.
SCANNER_UNIVERSE_MODE = "watchlist"   # "watchlist" (fast, reliable -- see SCANNER_WATCHLIST) or "sp500" (live Wikipedia fetch, ~500 tickers, slow and rate-limit-prone on cloud hosting)
SCANNER_WATCHLIST = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD"]
SCAN_INTERVAL_SECONDS = 300
NEWS_LOOKBACK_HOURS = 24
MIN_CONFIDENCE_SCORE = 60     # callouts scoring below this are discarded entirely, not just hidden in the UI
ALERT_MIN_CONFIDENCE = 75     # only email/SMS-alert on higher conviction than the dashboard's minimum
SWING_TARGET_DTE_DAYS = 21    # longer-dated option alt offered alongside the primary "buy shares" swing call
DEFAULT_IV_FALLBACK = 0.30    # rough placeholder IV for tickers not in DEFAULT_IV -- this is not calibrated to any real skew

# ---- Trend filter (swing timeframe requires the trade to agree with the longer-term
# trend -- historically the single biggest lever on real-world hit rate; a counter-trend
# swing signal is discarded outright rather than just scored lower) ----
TREND_FILTER_PERIOD = 50          # SMA period defining "the trend" for swing callouts
INTRADAY_TREND_FILTER_PERIOD = 20 # shorter SMA for intraday -- bonus only, not a hard gate
                                   # (intraday mean-reversion against the short trend is a
                                   # legitimate setup, unlike a counter-trend multi-day swing)

# ---- Exit plan (ATR-based for shares, so stops/targets scale with each ticker's actual
# recent volatility instead of one flat percentage across every name) ----
ATR_PERIOD = 14
ATR_STOP_MULT = 1.5     # shares stop-loss = entry -/+ ATR_STOP_MULT * ATR
ATR_TARGET_MULT = 2.5   # shares profit target = entry +/- ATR_TARGET_MULT * ATR (~1.7:1 reward/risk)
SWING_MAX_HOLD_DAYS = 10        # time-stop: re-evaluate the swing thesis if neither level hits by then
INTRADAY_MAX_HOLD_BARS = 12     # time-stop for the option alt, in units of the intraday interval (15m -> ~3h)

# ---- Confidence score point budget ----
# Split into two tiers so the backtest (scanner_backtest.py) can validate exactly the part
# that's computable from historical OHLCV alone, and clearly separate it from the live-only
# enrichments (news, earnings, sector) that free data sources can't backtest.
#
# Backtestable tier (technical, from OHLCV only):
AGREEMENT_PTS_PER_STRATEGY = 15   # per strategy that fires in the winning direction
AGREEMENT_MAX = 30                # cap (2 strategies configured per timeframe today)
RSI_BONUS = 10
VOLUME_BONUS = 10
TREND_FILTER_BONUS = 10
TECH_SCORE_MAX = AGREEMENT_MAX + RSI_BONUS + VOLUME_BONUS + TREND_FILTER_BONUS   # 60

# ---- Multi-timeframe confluence (intraday callouts only: does the DAILY trend, not just the
# 15m one, agree with the direction? real cross-timeframe confirmation, still OHLCV-only so
# it's included in what the backtest validates) ----
MTF_CONFLUENCE_BONUS = 10

# ---- Relative strength vs a benchmark (a genuine "is this a market leader or laggard"
# check, computed as excess return over a lookback window -- still OHLCV-only, so also
# included in the backtestable tier) ----
RS_BENCHMARK = "SPY"
RS_LOOKBACK_DAYS = 60
RS_OUTPERFORM_THRESHOLD = 0.05   # +/-5 percentage points of excess return counts as real confirmation
RS_BONUS = 10

BACKTESTABLE_SCORE_MAX = TECH_SCORE_MAX + MTF_CONFLUENCE_BONUS + RS_BONUS   # 80

# Live-only tier (NOT included in scanner_backtest.py -- no free historical news archive,
# and simulating "was there an earnings print N days after this specific historical bar"
# for hundreds of tickers across years is out of scope here):
# news sentiment: up to +/- NEWS_BONUS_MAX / NEWS_PENALTY_MAX (see scanner.py's _news_score)
NEWS_BONUS_MAX = 20
NEWS_PENALTY_MAX = 15
EARNINGS_BLACKOUT_DAYS = 5   # flag if an earnings print falls within max(this, the option's DTE)
EARNINGS_PENALTY = 10
SECTOR_CONFIRMATION_BONUS = 5   # best-effort: is the whole sector ETF moving the same way, not just this name?

# ---- Risk score (1-10) ----
# Deliberately separate from the confidence score above: confidence is "how much
# evidence supports this direction", risk is "how much could this cost you if wrong
# (or slow)". A high-confidence callout can still be high-risk.
RISK_ATR_HIGH_PCT = 0.045   # ATR/price at or above this scores as high volatility
RISK_ATR_MED_PCT = 0.02     # ATR/price at or above this scores as moderate volatility
RISK_DTE_VERY_SHORT = 3     # option DTE at/below this scores as very short-dated (theta/gamma risk)
RISK_DTE_SHORT = 10         # option DTE at/below this scores as short-dated
RISK_IV_HIGH = 0.40         # assumed IV at/above this scores as high (expensive premium, IV-crush exposure)
RISK_IV_MED = 0.25

# ---- Email alerts (Gmail SMTP) ----
# Set these as environment variables -- never hardcode credentials in this file:
#   GMAIL_ADDRESS       the Gmail account to send alerts from
#   GMAIL_APP_PASSWORD  a 16-character App Password (NOT your normal Gmail password);
#                        generate one at https://myaccount.google.com/apppasswords
#                        (requires 2-Step Verification enabled on that Google account)
#   ALERT_TO_EMAIL      recipient address (defaults to GMAIL_ADDRESS itself if unset)
# If these aren't set, alerts.py prints the alert to the console instead of failing.
