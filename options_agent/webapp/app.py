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
import json
import re
import threading
from functools import wraps
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # so sibling imports work

from flask import Flask, jsonify, render_template, request, session, redirect, url_for
import pandas as pd
import numpy as np

import config
from data_fetcher import fetch_daily, fetch_intraday, get_expirations, get_option_chain
from strategies import STRATEGY_FUNCS
from options_pricing import bs_price, select_strike
import market_scanner
import scanner_backtest

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


# interval -> (fetch function, valid periods for that interval)
TIMEFRAMES = {
    "1d": {"periods": ["3mo", "6mo", "1y", "3y", "5y"], "default_period": "1y"},
    "1h": {"periods": ["5d", "1mo", "60d"], "default_period": "1mo"},
    "15m": {"periods": ["5d", "1mo", "60d"], "default_period": "1mo"},
    "5m": {"periods": ["5d", "1mo", "60d"], "default_period": "1mo"},
}


def _get_data(ticker, strategy_name, interval="1d", period=None):
    """Fetch + compute signals for a given interval/period, with a short cache."""
    period = period or TIMEFRAMES.get(interval, TIMEFRAMES["1d"])["default_period"]
    cache_key = (ticker, strategy_name, interval, period)
    now = datetime.now()
    if cache_key in _cache:
        cached_time, cached_df, is_synthetic = _cache[cache_key]
        if (now - cached_time).total_seconds() < CACHE_TTL_SECONDS:
            return cached_df, is_synthetic

    is_synthetic = False
    try:
        if interval == "1d":
            df = fetch_daily(ticker, period=period)
        else:
            df = fetch_intraday(ticker, interval=interval, period=period)
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
    interval = request.args.get("interval", "1d")
    period = request.args.get("period")
    if strategy_name not in config.STRATEGIES:
        return jsonify({"error": f"unknown strategy {strategy_name}"}), 400
    if interval not in TIMEFRAMES:
        return jsonify({"error": f"unknown interval {interval}"}), 400

    df, is_synthetic = _get_data(ticker, strategy_name, interval, period)

    time_fmt = "%Y-%m-%d" if interval == "1d" else "%Y-%m-%dT%H:%M:%S"
    candles = [
        {"time": idx.strftime(time_fmt), "open": round(r.open, 2), "high": round(r.high, 2),
         "low": round(r.low, 2), "close": round(r.close, 2)}
        for idx, r in df.iterrows()
    ]

    markers = []
    for idx, r in df.iterrows():
        if r.get("signal") == 1:
            markers.append({"time": idx.strftime(time_fmt), "position": "belowBar",
                             "color": "#00d9a3", "shape": "arrowUp", "text": "BUY"})
        elif r.get("signal") == -1:
            markers.append({"time": idx.strftime(time_fmt), "position": "aboveBar",
                             "color": "#ff4d5e", "shape": "arrowDown", "text": "SELL"})

    overlays = {}
    if strategy_name == "ma_rsi":
        overlays["fast_ma"] = _line_series(df, "fast_ma", time_fmt)
        overlays["slow_ma"] = _line_series(df, "slow_ma", time_fmt)
    elif strategy_name == "bb_squeeze_breakout":
        overlays["bb_upper"] = _line_series(df, "bb_upper", time_fmt)
        overlays["bb_mid"] = _line_series(df, "bb_mid", time_fmt)
        overlays["bb_lower"] = _line_series(df, "bb_lower", time_fmt)

    return jsonify({
        "ticker": ticker, "strategy": strategy_name, "is_synthetic": is_synthetic,
        "interval": interval, "period": period or TIMEFRAMES[interval]["default_period"],
        "candles": candles, "markers": markers, "overlays": overlays,
    })


def _line_series(df, col, time_fmt="%Y-%m-%d"):
    out = []
    for idx, val in df[col].items():
        if pd.notna(val):
            out.append({"time": idx.strftime(time_fmt), "value": round(float(val), 2)})
    return out


@app.route("/api/alert/<ticker>")
@login_required
def alert(ticker):
    strategy_name = request.args.get("strategy", "ma_rsi")
    if strategy_name not in config.STRATEGIES:
        return jsonify({"error": f"unknown strategy {strategy_name}"}), 400

    df, is_synthetic = _get_data(ticker, strategy_name, interval="1d")
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


@app.route("/api/expirations/<ticker>")
@login_required
def expirations(ticker):
    """Real available expiration dates for this ticker, right now."""
    try:
        exps = get_expirations(ticker)
        return jsonify({"ticker": ticker, "expirations": exps, "is_synthetic": False})
    except Exception:
        today = datetime.now()
        fallback_exps = [(today + timedelta(days=d)).strftime("%Y-%m-%d") for d in (1, 3, 8, 15, 29)]
        return jsonify({"ticker": ticker, "expirations": fallback_exps, "is_synthetic": True})


@app.route("/api/chain/<ticker>")
@login_required
def chain(ticker):
    """Real live options chain for one expiration, filtered to strikes near spot.
    This is a live snapshot from the exchange via yfinance -- real bid/ask/last,
    not a Black-Scholes estimate -- but it reflects THIS MOMENT, not history."""
    expiration = request.args.get("expiration")
    if not expiration:
        return jsonify({"error": "expiration query param required"}), 400

    try:
        calls, puts = get_option_chain(ticker, expiration)
        df, _ = _get_data(ticker, "ma_rsi", interval="1d")
        spot = float(df.iloc[-1]["close"])

        near_calls = calls[(calls["strike"] >= spot * 0.9) & (calls["strike"] <= spot * 1.1)]
        near_puts = puts[(puts["strike"] >= spot * 0.9) & (puts["strike"] <= spot * 1.1)]

        return jsonify({
            "ticker": ticker, "expiration": expiration, "spot": round(spot, 2), "is_synthetic": False,
            "calls": near_calls.round(2).to_dict(orient="records"),
            "puts": near_puts.round(2).to_dict(orient="records"),
        })
    except Exception:
        df, _ = _get_data(ticker, "ma_rsi", interval="1d")
        spot = float(df.iloc[-1]["close"])
        iv = config.DEFAULT_IV.get(ticker, 0.16)
        try:
            dte = max((datetime.strptime(expiration, "%Y-%m-%d") - datetime.now()).days, 0)
        except ValueError:
            dte = config.TARGET_DTE_DAYS

        strikes = sorted(set(round(spot * (1 + pct)) for pct in (-0.04, -0.02, -0.01, 0, 0.01, 0.02, 0.04)))
        calls_out, puts_out = [], []
        for k in strikes:
            calls_out.append({"strike": k, "lastPrice": round(bs_price(spot, k, dte, iv, config.RISK_FREE_RATE, "call"), 2),
                               "bid": None, "ask": None, "volume": None, "openInterest": None, "impliedVolatility": round(iv, 2)})
            puts_out.append({"strike": k, "lastPrice": round(bs_price(spot, k, dte, iv, config.RISK_FREE_RATE, "put"), 2),
                              "bid": None, "ask": None, "volume": None, "openInterest": None, "impliedVolatility": round(iv, 2)})

        return jsonify({
            "ticker": ticker, "expiration": expiration, "spot": round(spot, 2), "is_synthetic": True,
            "calls": calls_out, "puts": puts_out,
        })


SCANNER_RESULTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scanner_results.json"
)


@app.route("/api/watchlist")
@login_required
def watchlist():
    """What the scanner is CURRENTLY configured to scan (independent of
    whether it's run yet), plus a curated ticker list for the dashboard's
    picker UI -- see config.TOP_30_MOST_TRADED for what it is and isn't."""
    from universe import get_scan_universe
    return jsonify({
        "mode": config.SCANNER_UNIVERSE_MODE,
        "tickers": get_scan_universe(),
        "top30": config.TOP_30_MOST_TRADED,
    })


@app.route("/api/scanner")
@login_required
def scanner_results():
    """Reads the latest scan output -- either from the "Scan Now" button
    (see /api/scan below) or from python market_scanner.py run separately."""
    if not os.path.exists(SCANNER_RESULTS_PATH):
        return jsonify({
            "generated_at": None, "universe_size": 0, "tickers_scanned": [], "cycle_seconds": None, "callouts": [],
            "note": "Scanner hasn't run yet. Start it separately with: python market_scanner.py",
        })
    with open(SCANNER_RESULTS_PATH) as f:
        return jsonify(json.load(f))


BACKTEST_RESULTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scanner_backtest_results.json"
)


@app.route("/api/backtest")
@login_required
def backtest_results():
    """Reads the latest backtest output -- either from the "Run Backtest"
    button (see /api/backtest/run below) or from python scanner_backtest.py
    run separately."""
    if not os.path.exists(BACKTEST_RESULTS_PATH):
        return jsonify({
            "generated_at": None, "report": {},
            "note": "Backtest hasn't run yet. Click \"Run Backtest\" above, or start it separately with: python scanner_backtest.py",
        })
    with open(BACKTEST_RESULTS_PATH) as f:
        return jsonify(json.load(f))


# ---- Background-triggered scan / backtest, so nothing requires a terminal ----
# A full universe sweep or multi-year backtest is too slow for a single request
# (could be minutes), so each button starts a background thread and the page
# polls the matching /status endpoint until it finishes, then re-fetches the
# results route above. Only one of each job runs at a time; a second click
# while one is running is reported as still-running rather than queued or
# stacked, so repeated clicks can't launch overlapping sweeps.
_scan_lock = threading.Lock()
_scan_job = {"running": False, "started_at": None, "finished_at": None, "error": None}

_backtest_lock = threading.Lock()
_backtest_job = {"running": False, "started_at": None, "finished_at": None, "error": None}


MAX_CUSTOM_SCAN_TICKERS = 50  # a picker-driven one-off scan is meant to be quick, not a backdoor to a full-universe sweep
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")  # generous but bounded: real tickers, not arbitrary input


def _sanitize_tickers(raw):
    seen, out = set(), []
    for t in raw:
        t = str(t).strip().upper()
        if t and _TICKER_RE.match(t) and t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= MAX_CUSTOM_SCAN_TICKERS:
            break
    return out


def _run_scan_job(tickers=None):
    try:
        market_scanner.run_once(tickers=tickers)
        _scan_job["error"] = None
    except Exception as e:
        _scan_job["error"] = str(e)
    finally:
        _scan_job["running"] = False
        _scan_job["finished_at"] = datetime.now().isoformat(timespec="seconds")


@app.route("/api/scan", methods=["POST"])
@login_required
def start_scan():
    payload = request.get_json(silent=True) or {}
    custom_tickers = _sanitize_tickers(payload["tickers"]) if payload.get("tickers") else None
    if payload.get("tickers") and not custom_tickers:
        return jsonify({"status": "error", "error": "No valid tickers in request"}), 400

    if not _scan_lock.acquire(blocking=False):
        return jsonify({"status": "already_running", "job": _scan_job}), 409
    try:
        if _scan_job["running"]:
            return jsonify({"status": "already_running", "job": _scan_job}), 409
        _scan_job.update(running=True, started_at=datetime.now().isoformat(timespec="seconds"),
                          finished_at=None, error=None)
        threading.Thread(target=_run_scan_job, kwargs={"tickers": custom_tickers}, daemon=True).start()
        return jsonify({"status": "started", "job": _scan_job})
    finally:
        _scan_lock.release()


@app.route("/api/scan/status")
@login_required
def scan_status():
    return jsonify(_scan_job)


def _run_backtest_job():
    try:
        scanner_backtest.run_and_save()
        _backtest_job["error"] = None
    except Exception as e:
        _backtest_job["error"] = str(e)
    finally:
        _backtest_job["running"] = False
        _backtest_job["finished_at"] = datetime.now().isoformat(timespec="seconds")


@app.route("/api/backtest/run", methods=["POST"])
@login_required
def start_backtest():
    if not _backtest_lock.acquire(blocking=False):
        return jsonify({"status": "already_running", "job": _backtest_job}), 409
    try:
        if _backtest_job["running"]:
            return jsonify({"status": "already_running", "job": _backtest_job}), 409
        _backtest_job.update(running=True, started_at=datetime.now().isoformat(timespec="seconds"),
                              finished_at=None, error=None)
        threading.Thread(target=_run_backtest_job, daemon=True).start()
        return jsonify({"status": "started", "job": _backtest_job})
    finally:
        _backtest_lock.release()


@app.route("/api/backtest/status")
@login_required
def backtest_status():
    return jsonify(_backtest_job)


if __name__ == "__main__":
    app.run(debug=True, port=5000, host="0.0.0.0", threaded=True)
