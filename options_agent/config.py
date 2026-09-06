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
SCANNER_UNIVERSE_MODE = "sp500"    # "sp500" (live Wikipedia fetch, falls back to a curated liquid subset -- see universe.py) or "watchlist"
SCANNER_WATCHLIST = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD"]
SCAN_INTERVAL_SECONDS = 300
NEWS_LOOKBACK_HOURS = 24
MIN_CONFIDENCE_SCORE = 60     # callouts scoring below this are discarded entirely, not just hidden in the UI
ALERT_MIN_CONFIDENCE = 75     # only email/SMS-alert on higher conviction than the dashboard's minimum
SWING_TARGET_DTE_DAYS = 21    # longer-dated option alt offered alongside the primary "buy shares" swing call
DEFAULT_IV_FALLBACK = 0.30    # rough placeholder IV for tickers not in DEFAULT_IV -- this is not calibrated to any real skew

# ---- Email alerts (Gmail SMTP) ----
# Set these as environment variables -- never hardcode credentials in this file:
#   GMAIL_ADDRESS       the Gmail account to send alerts from
#   GMAIL_APP_PASSWORD  a 16-character App Password (NOT your normal Gmail password);
#                        generate one at https://myaccount.google.com/apppasswords
#                        (requires 2-Step Verification enabled on that Google account)
#   ALERT_TO_EMAIL      recipient address (defaults to GMAIL_ADDRESS itself if unset)
# If these aren't set, alerts.py prints the alert to the console instead of failing.
