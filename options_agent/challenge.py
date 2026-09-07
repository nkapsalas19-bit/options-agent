"""
Trading Challenge: a goal-oriented paper-trading tracker layered on top of
the scanner. Set a start/end date, a starting budget, a target goal amount,
and an instrument preference (shares/option/both); get position sizes for
live scanner callouts that actually fit your budget and a per-trade risk
cap; add the ones you "take" to a tracked ledger.

Open positions resolve automatically -- market_scanner.run_once() calls
check_open_positions() at the end of every scan cycle, so there's no
separate process to run. Resolution reuses the exact convention
scanner_backtest.py already uses (does the underlying's daily high/low
cross the target or stop since entry; a same-period ambiguous case
resolves to the stop, conservative by design) rather than inventing a new
one. Positions open longer than their planned hold window are closed at
the last available price with reason "time_stop", matching the exit
plan's own time-stop rule instead of running forever.

Still paper trading: no real broker, no real fills, no real bid-ask
spread. Option P&L is Black-Scholes re-priced at the ACTUAL exit date and
underlying price (same approach as scanner.py's own "price thesis"), not
a fixed premium guessed at recommendation time -- more accurate than
reusing a stale number, but still an approximation; see options_pricing.py.
"""
import json
import os
from datetime import datetime, date, timedelta

import config
from data_fetcher import fetch_daily, fetch_intraday
from options_pricing import bs_price

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "challenge_config.json")
TRADES_PATH = os.path.join(os.path.dirname(__file__), "challenge_trades.json")

VALID_INSTRUMENT_PREFS = ("shares", "option", "both")


def _load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def _save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def set_challenge(start_date, end_date, starting_budget, goal_amount, instrument_pref, risk_pct_per_trade=3.0):
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    if end <= start:
        raise ValueError("end_date must be after start_date")
    starting_budget = float(starting_budget)
    goal_amount = float(goal_amount)
    if starting_budget <= 0:
        raise ValueError("starting_budget must be positive")
    if instrument_pref not in VALID_INSTRUMENT_PREFS:
        raise ValueError(f"instrument_pref must be one of {VALID_INSTRUMENT_PREFS}")
    risk_pct_per_trade = max(0.5, min(float(risk_pct_per_trade), 25.0))  # sane bounds -- not a hard trading limit, just guards against fat-finger inputs like "300"

    cfg = {
        "start_date": start_date, "end_date": end_date,
        "starting_budget": starting_budget, "goal_amount": goal_amount,
        "instrument_pref": instrument_pref,
        "risk_pct_per_trade": risk_pct_per_trade,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    _save_json(CONFIG_PATH, cfg)
    _save_json(TRADES_PATH, [])  # a new challenge starts with a clean ledger
    return cfg


def get_challenge():
    return _load_json(CONFIG_PATH, None)


def clear_challenge():
    for p in (CONFIG_PATH, TRADES_PATH):
        if os.path.exists(p):
            os.remove(p)


def _cash_balance(trades, starting_budget):
    balance = starting_budget
    for t in trades:
        balance -= t["cost"]
        if t["status"] == "closed":
            balance += t["proceeds"]
    return balance


def get_summary():
    cfg = get_challenge()
    if not cfg:
        return None
    trades = _load_json(TRADES_PATH, [])
    cash = _cash_balance(trades, cfg["starting_budget"])
    open_cost_basis = sum(t["cost"] for t in trades if t["status"] == "open")
    equity = cash + open_cost_basis  # open positions marked at cost, not re-priced live -- a rough but honest mark
    realized_pnl = sum(t["proceeds"] - t["cost"] for t in trades if t["status"] == "closed")

    today = date.today()
    start = datetime.strptime(cfg["start_date"], "%Y-%m-%d").date()
    end = datetime.strptime(cfg["end_date"], "%Y-%m-%d").date()
    total_days = max((end - start).days, 1)
    elapsed_days = max(min((today - start).days, total_days), 0)
    days_remaining = max((end - today).days, 0)

    goal_span = cfg["goal_amount"] - cfg["starting_budget"]
    progress_pct = ((equity - cfg["starting_budget"]) / goal_span * 100) if goal_span != 0 else (
        100.0 if equity >= cfg["starting_budget"] else 0.0
    )
    time_pct = elapsed_days / total_days * 100

    return {
        "config": cfg,
        "cash_balance": round(cash, 2),
        "equity": round(equity, 2),
        "realized_pnl": round(realized_pnl, 2),
        "open_positions": [t for t in trades if t["status"] == "open"],
        "closed_positions": [t for t in trades if t["status"] == "closed"],
        "progress_pct": round(max(min(progress_pct, 999.0), -999.0), 1),
        "time_pct": round(time_pct, 1),
        "days_remaining": days_remaining,
        "is_expired": today > end,
        "on_pace": progress_pct >= time_pct,
    }


def suggest_position_size(callout, cfg, current_balance):
    """Returns a sizing dict, or None if nothing affordable/appropriate fits
    (budget too small for even 1 unit, or the callout's instrument doesn't
    match the preference)."""
    pref = cfg["instrument_pref"]
    exit_plan = callout["exit_plan"]
    risk_budget = current_balance * (cfg["risk_pct_per_trade"] / 100.0)

    want_shares = pref == "shares" or (pref == "both" and callout["instrument_primary"] == "shares")
    if want_shares:
        entry = exit_plan["entry_price"]
        risk_per_unit = exit_plan["shares"]["risk_per_share"]
        if risk_per_unit <= 0 or entry <= 0:
            return None
        qty = min(int(risk_budget // risk_per_unit), int(current_balance // entry))
        if qty < 1:
            return None
        return {
            "instrument": "shares", "qty": qty,
            "entry_price": entry, "target_price": exit_plan["shares"]["profit_target"],
            "stop_price": exit_plan["shares"]["stop_loss"],
            "cost": round(qty * entry, 2), "risk_dollars": round(qty * risk_per_unit, 2),
            "max_hold_days": config.SWING_MAX_HOLD_DAYS,
        }

    option = exit_plan.get("option")
    if not option:
        return None
    entry = option["entry_price"]
    risk_per_contract = (entry - option["stop_loss"]) * 100
    cost_per_contract = entry * 100
    if risk_per_contract <= 0 or cost_per_contract <= 0:
        return None
    qty = min(int(risk_budget // risk_per_contract), int(current_balance // cost_per_contract))
    if qty < 1:
        return None
    alt = callout["option_alt"]
    return {
        "instrument": "option", "qty": qty,
        "entry_price": entry, "cost": round(qty * cost_per_contract, 2),
        "risk_dollars": round(qty * risk_per_contract, 2),
        "strike": alt["strike"], "option_type": alt["type"], "dte_days": alt["dte_days"],
        "expiration_date": alt["expiration_date"], "iv_assumed": alt["iv_assumed"],
        "underlying_entry": callout["spot"],
        "underlying_target": exit_plan["shares"]["profit_target"],
        "underlying_stop": exit_plan["shares"]["stop_loss"],
    }


def add_trade(ticker, timeframe, sizing):
    trades = _load_json(TRADES_PATH, [])
    now = datetime.now()
    trade = {
        "id": (trades[-1]["id"] + 1) if trades else 1,
        "ticker": ticker, "timeframe": timeframe,
        "instrument": sizing["instrument"], "qty": sizing["qty"],
        "entry_price": sizing["entry_price"], "entry_date": date.today().isoformat(),
        "entry_time": now.isoformat(timespec="seconds"),  # naive local time; used for intraday same-day resolution
        "cost": sizing["cost"], "status": "open", "proceeds": None,
        "exit_price": None, "exit_date": None, "exit_reason": None,
    }
    if sizing["instrument"] == "shares":
        trade.update(
            target_price=sizing["target_price"], stop_price=sizing["stop_price"],
            max_hold_days=sizing["max_hold_days"],
        )
    else:
        trade.update(
            strike=sizing["strike"], option_type=sizing["option_type"], dte_days=sizing["dte_days"],
            expiration_date=sizing["expiration_date"], iv_assumed=sizing["iv_assumed"],
            underlying_entry=sizing["underlying_entry"], underlying_target=sizing["underlying_target"],
            underlying_stop=sizing["underlying_stop"],
        )
    trades.append(trade)
    _save_json(TRADES_PATH, trades)
    return trade


def _entry_time(t):
    """Trades added before entry_time was tracked only have entry_date --
    fall back to midnight of that day (same as the old behavior)."""
    if t.get("entry_time"):
        return datetime.fromisoformat(t["entry_time"])
    return datetime.strptime(t["entry_date"], "%Y-%m-%d")


def _recent_bars_for(t):
    """Bars strictly after entry, using whichever granularity matches how
    the position is actually meant to resolve: 15-minute bars for intraday
    (options-only) positions so a same-day target/stop/time-stop can
    trigger, daily bars for swing positions. Returns (recent_df, entry_time)
    or (None, entry_time) if nothing fetchable/new yet."""
    entry_time = _entry_time(t)
    if t.get("timeframe") == "intraday":
        df = fetch_intraday(t["ticker"], interval="15m", period="5d")
        if df.index.tz is not None:
            df = df.tz_localize(None)
        recent = df[df.index > entry_time]
    else:
        df = fetch_daily(t["ticker"], period="6mo")
        entry_date = entry_time.date()
        recent = df[df.index.date > entry_date]
    return (recent if not recent.empty else None), entry_time


def _resolve_shares(t, recent, entry_time):
    direction = 1 if t["target_price"] > t["entry_price"] else -1
    hit_stop = (recent["low"] <= t["stop_price"]).any() if direction == 1 else (recent["high"] >= t["stop_price"]).any()
    hit_target = (recent["high"] >= t["target_price"]).any() if direction == 1 else (recent["low"] <= t["target_price"]).any()

    if hit_stop:  # same convention as scanner_backtest.py: an ambiguous same-window case resolves to the stop
        return t["stop_price"], "stop", t["qty"] * t["stop_price"]
    if hit_target:
        return t["target_price"], "target", t["qty"] * t["target_price"]

    days_held = (date.today() - entry_time.date()).days
    if days_held >= t["max_hold_days"]:
        last_price = float(recent["close"].iloc[-1])
        return last_price, "time_stop", t["qty"] * last_price
    return None


def _resolve_option(t, recent, entry_time):
    direction = 1 if t["underlying_target"] > t["underlying_entry"] else -1
    hit_stop = (recent["low"] <= t["underlying_stop"]).any() if direction == 1 else (recent["high"] >= t["underlying_stop"]).any()
    hit_target = (recent["high"] >= t["underlying_target"]).any() if direction == 1 else (recent["low"] <= t["underlying_target"]).any()

    if t.get("timeframe") == "intraday":
        minutes_held = (datetime.now() - entry_time).total_seconds() / 60.0
        timed_out = minutes_held >= config.INTRADAY_MAX_HOLD_BARS * 15
        days_held = minutes_held / (60 * 24)  # for the remaining-DTE estimate below only
    else:
        days_held = (date.today() - entry_time.date()).days
        timed_out = days_held >= t["dte_days"]

    exit_underlying, reason = None, None
    if hit_stop:
        exit_underlying, reason = t["underlying_stop"], "stop"
    elif hit_target:
        exit_underlying, reason = t["underlying_target"], "target"
    elif timed_out:
        exit_underlying, reason = float(recent["close"].iloc[-1]), "time_stop"
    else:
        return None

    remaining_dte = max(t["dte_days"] - days_held, 0.5)
    exit_premium = bs_price(exit_underlying, t["strike"], remaining_dte, t["iv_assumed"],
                             config.RISK_FREE_RATE, t["option_type"])
    return exit_premium, reason, t["qty"] * exit_premium * 100


def check_open_positions():
    """Fetches fresh data for each open position's ticker and resolves any
    that have hit target/stop/time-stop since entry. Safe to call every
    scan cycle -- tickers with nothing new just no-op. Returns
    (all_trades, newly_closed_trades) so callers (market_scanner.py) can
    notify only about positions that closed just now."""
    trades = _load_json(TRADES_PATH, [])
    newly_closed = []
    for t in trades:
        if t["status"] != "open":
            continue
        try:
            recent, entry_time = _recent_bars_for(t)
        except Exception:
            continue
        if recent is None:
            continue

        resolver = _resolve_shares if t["instrument"] == "shares" else _resolve_option
        result = resolver(t, recent, entry_time)
        if result is None:
            continue

        exit_price, reason, proceeds = result
        t["status"] = "closed"
        t["exit_price"] = round(exit_price, 2)
        t["exit_date"] = date.today().isoformat()
        t["exit_reason"] = reason
        t["proceeds"] = round(proceeds, 2)
        newly_closed.append(t)

    if newly_closed:
        _save_json(TRADES_PATH, trades)
    return trades, newly_closed
