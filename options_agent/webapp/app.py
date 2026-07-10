"""
Flask backend for the live dashboard. Serves:
  GET /login                   -> password gate
  GET /                        -> the dashboard page (requires login)
  GET /api/chart/<ticker>      -> OHLCV + overlay lines + buy/sell markers (requires login)
  GET /api/alert/<ticker>      -> current signal state: suggestion, strike, expiration, entry/target/stop (requires login)

Run with: python webapp/app.py   (from the options_agent/ directory, since it
imports the sibling modules: data_fetcher, strategies, indicators, options_pricing, config)

IMPORTANT: you must run this file with Python and visit the URL it prints
(http://localhost:5000). Double-clicking templates/index.html directly in a
browser will NOT work -- there's no server behind it that way, so every
chart/data request fails silently. This is almost certainly why you saw
"an error" and no charts: the HTML file was opened standalone instead of
through Flask.
"""
import sys
import os
from functools import wraps
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # so sibling imports work

from flask import Flask, jsonify, render_template, request, session, redirect, url_for
import pandas as pd
import numpy as np

import config
from data_fetcher import fetch_daily
from strategies import STRATEGY_FUNCS
from options_pricing import bs_price, select_strike

app = Flask(__name__, template_folder="templates", static_folder="static")

# --- Password protection ---
# Set these as environment variables before hosting publicly. Defaults exist
# so it runs out of the box locally, but CHANGE THE PASSWORD before deploying
# anywhere reachable from the internet.
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-before-hosting")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "changeme123")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == DASHBOARD_PASSWORD:
            session["authenticated"] = True
            session.permanent = True
            return redirect(url_for("index"))
        error = "Incorrect password"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.pop("authenticated", None)
    return redirect(url_for("login"))


_cache = {}
CACHE_TTL_SECONDS = 60


def _get_data(ticker, strategy_name):
    """Fetch + compute signals, with a short cache so the frontend can poll
    frequently without hammering yfinance. Falls back to synthetic data if
    the real fetch fails (e.g. no network), clearly flagged in the response."""
    cache_key = (ticker, strategy_name)
    now = datetime.now()
    if cache_key in _cache:
        cached_time, cached_df, is_synthetic = _cache[cache_key]
        if (now - cached_time).total_seconds() < CACHE_TTL_SECONDS:
            return cached_df, is_synthetic

    is_synthetic = False
    try:
        df = fetch_daily(ticker, period=config.BACKTEST_PERIOD)
    except Exception:
        df = _synthetic_fallback(ticker)
        is_synthetic = True

    params = config.STRATEGIES[strategy_name]
    df = STRATEGY_FUNCS[strategy_name](df, params)
    _cache[cache_key] = (now, df, is_synthetic)
    return df, is_synthetic


def _synthetic_fallback(ticker, days=500, seed=None):
    """Used only if real data fetch fails, so the dashboard still renders.
    Clearly flagged to the frontend as synthetic -- never silently passed off as real."""
    seed = seed or (hash(ticker) % 1000)
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=datetime.now(), periods=days)
    base = 500 if ticker == "SPY" else 450
    returns = rng.normal(0.0003, 0.011, days)
    prices = base * np.cumprod(1 + returns)
    high = prices * (1 + np.abs(rng.normal(0, 0.004, days)))
    low = prices * (1 - np.abs(rng.normal(0, 0.004, days)))
    open_ = prices * (1 + rng.normal(0, 0.002, days))
    volume = rng.integers(40_000_000, 90_000_000, days)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": prices, "volume": volume}, index=dates)


@app.route("/")
@login_required
def index():
    return render_template("index.html", tickers=config.TICKERS, strategies=list(config.STRATEGIES.keys()))


@app.route("/api/chart/<ticker>")
@login_required
def chart_data(ticker):
    strategy_name = request.args.get("strategy", "ma_rsi")
    if strategy_name not in config.STRATEGIES:
        return jsonify({"error": f"unknown strategy {strategy_name}"}), 400

    df, is_synthetic = _get_data(ticker, strategy_name)

    candles = [
        {"time": idx.strftime("%Y-%m-%d"), "open": round(r.open, 2), "high": round(r.high, 2),
         "low": round(r.low, 2), "close": round(r.close, 2)}
        for idx, r in df.iterrows()
    ]

    markers = []
    for idx, r in df.iterrows():
        if r.get("signal") == 1:
            markers.append({"time": idx.strftime("%Y-%m-%d"), "position": "belowBar",
                             "color": "#00d9a3", "shape": "arrowUp", "text": "BUY"})
        elif r.get("signal") == -1:
            markers.append({"time": idx.strftime("%Y-%m-%d"), "position": "aboveBar",
                             "color": "#ff4d5e", "shape": "arrowDown", "text": "SELL"})

    overlays = {}
    if strategy_name == "ma_rsi":
        overlays["fast_ma"] = _line_series(df, "fast_ma")
        overlays["slow_ma"] = _line_series(df, "slow_ma")
    elif strategy_name == "bb_squeeze_breakout":
        overlays["bb_upper"] = _line_series(df, "bb_upper")
        overlays["bb_mid"] = _line_series(df, "bb_mid")
        overlays["bb_lower"] = _line_series(df, "bb_lower")

    return jsonify({
        "ticker": ticker, "strategy": strategy_name, "is_synthetic": is_synthetic,
        "candles": candles, "markers": markers, "overlays": overlays,
    })


def _line_series(df, col):
    out = []
    for idx, val in df[col].items():
        if pd.notna(val):
            out.append({"time": idx.strftime("%Y-%m-%d"), "value": round(float(val), 2)})
    return out


@app.route("/api/alert/<ticker>")
@login_required
def alert(ticker):
    strategy_name = request.args.get("strategy", "ma_rsi")
    if strategy_name not in config.STRATEGIES:
        return jsonify({"error": f"unknown strategy {strategy_name}"}), 400

    df, is_synthetic = _get_data(ticker, strategy_name)
    latest = df.iloc[-1]
    spot = float(latest["close"])

    # Trend state: simple regime check, not just the raw signal (a signal only
    # fires on the crossover bar; trend state answers "what's true right now")
    if strategy_name == "ma_rsi":
        trending_up = latest.get("fast_ma", np.nan) > latest.get("slow_ma", np.nan)
        market_state = "TRENDING" if abs(latest.get("fast_ma", 0) - latest.get("slow_ma", 0)) / spot > 0.002 else "RANGING"
        live_trend = "BULLISH" if trending_up else "BEARISH"
    elif strategy_name == "bb_squeeze_breakout":
        trending_up = spot > latest.get("bb_mid", spot)
        market_state = "TRENDING"
        live_trend = "BULLISH" if trending_up else "BEARISH"
    else:
        trending_up = True
        market_state = "TRENDING"
        live_trend = "BULLISH"

    # look back a few bars for the most recent actual entry signal to decide the suggestion
    recent_signal = 0
    lookback = min(5, len(df))
    for i in range(1, lookback + 1):
        s = df.iloc[-i]["signal"]
        if s != 0:
            recent_signal = s
            break

    suggestion = "CALL" if recent_signal == 1 else ("PUT" if recent_signal == -1 else "NONE")
    option_type = "call" if recent_signal == 1 else "put"

    strike = select_strike(spot, config.STRIKE_OFFSET_PCT, option_type)
    iv = config.DEFAULT_IV.get(ticker, 0.16)
    entry_price = bs_price(spot, strike, config.TARGET_DTE_DAYS, iv, config.RISK_FREE_RATE, option_type)
    target_price = entry_price * (1 + config.PROFIT_TARGET_PCT)
    stop_price = entry_price * (1 - config.STOP_LOSS_PCT)
    expiration = (datetime.now() + timedelta(days=config.TARGET_DTE_DAYS)).strftime("%Y-%m-%d")

    return jsonify({
        "ticker": ticker,
        "strategy": strategy_name,
        "is_synthetic": is_synthetic,
        "spot": round(spot, 2),
        "market_state": market_state,
        "live_trend": live_trend,
        "suggestion": suggestion,
        "option_type": option_type.upper() if suggestion != "NONE" else None,
        "strike": strike if suggestion != "NONE" else None,
        "expiration": expiration if suggestion != "NONE" else None,
        "entry_est_price": round(entry_price, 2) if suggestion != "NONE" else None,
        "target_est_price": round(target_price, 2) if suggestion != "NONE" else None,
        "stop_est_price": round(stop_price, 2) if suggestion != "NONE" else None,
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000, host="0.0.0.0")
