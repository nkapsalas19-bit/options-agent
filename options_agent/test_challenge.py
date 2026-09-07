"""
Validates challenge.py's sizing, ledger, and auto-resolution logic against
constructed callouts and stubbed price data. Proves the mechanics are
correct (position sizing respects the risk cap and budget, trades resolve
to the right outcome, P&L math is right, option exits are re-priced at the
actual exit date/price rather than reusing a stale premium guess) -- it
says nothing about whether any of this makes money. This is a paper-trading
tracker, not a guarantee.
"""
from datetime import date, datetime, timedelta

import pandas as pd

import challenge


def make_shares_callout(entry=100.0, target=106.0, stop=97.0):
    return {
        "ticker": "TEST", "instrument_primary": "shares", "spot": entry,
        "exit_plan": {
            "entry_price": entry,
            "shares": {"profit_target": target, "stop_loss": stop, "risk_per_share": entry - stop,
                       "reward_per_share": target - entry, "reward_risk_ratio": (target - entry) / (entry - stop)},
            "option": {"entry_price": 3.5, "profit_target": 5.25, "stop_loss": 2.28},
        },
        "option_alt": {"strike": round(entry), "type": "call", "dte_days": 21,
                       "expiration_date": (date.today() + timedelta(days=21)).isoformat(), "iv_assumed": 0.3},
    }


def flat_then_move_df(days=5, jump_high=None, jump_low=None, jump_close=None):
    dates = pd.bdate_range(end=pd.Timestamp.today() + pd.Timedelta(days=1), periods=days)
    high = [101] * days
    low = [99] * days
    close = [100] * days
    if jump_high is not None:
        high[1:] = [jump_high] * (days - 1)
    if jump_low is not None:
        low[1:] = [jump_low] * (days - 1)
    if jump_close is not None:
        close[1:] = [jump_close] * (days - 1)
    return pd.DataFrame({"open": close, "high": high, "low": low, "close": close, "volume": 1_000_000}, index=dates)


def flat_then_move_intraday_df(bars=6, jump_high=None, jump_low=None, jump_close=None):
    """15-minute bars starting a few minutes in the future (so they land
    strictly after "now", matching what a real fetch would return once time
    has passed since entry) -- lets intraday same-day resolution be tested
    without waiting on a real clock."""
    dates = pd.date_range(start=pd.Timestamp.now() + pd.Timedelta(minutes=1), periods=bars, freq="15min")
    high = [101] * bars
    low = [99] * bars
    close = [100] * bars
    if jump_high is not None:
        high[1:] = [jump_high] * (bars - 1)
    if jump_low is not None:
        low[1:] = [jump_low] * (bars - 1)
    if jump_close is not None:
        close[1:] = [jump_close] * (bars - 1)
    return pd.DataFrame({"open": close, "high": high, "low": low, "close": close, "volume": 100_000}, index=dates)


def main():
    print("=== 1. Position sizing respects the risk cap ===")
    challenge.clear_challenge()
    cfg = challenge.set_challenge(date.today().isoformat(), (date.today() + timedelta(days=30)).isoformat(),
                                   1000, 2000, "shares", risk_pct_per_trade=3.0)
    callout = make_shares_callout()
    sizing = challenge.suggest_position_size(callout, cfg, current_balance=1000)
    print(sizing)
    # risk budget = 1000 * 3% = 30; risk_per_share = 3.0 -> qty should be 10, not more
    assert sizing["qty"] == 10
    assert sizing["risk_dollars"] <= 30.0 + 1e-9
    assert sizing["cost"] <= 1000

    print("\n=== 2. Sizing declines (returns None) when it doesn't fit the budget at all ===")
    tiny_sizing = challenge.suggest_position_size(callout, cfg, current_balance=2.0)
    print(tiny_sizing)
    assert tiny_sizing is None

    print("\n=== 3. Instrument preference is respected ('option' pref against a shares-primary callout still sizes the option leg) ===")
    cfg_opt = challenge.set_challenge(date.today().isoformat(), (date.today() + timedelta(days=30)).isoformat(),
                                       5000, 8000, "option", risk_pct_per_trade=5.0)
    opt_sizing = challenge.suggest_position_size(callout, cfg_opt, current_balance=5000)
    print(opt_sizing)
    assert opt_sizing["instrument"] == "option"

    print("\n=== 4. Adding a trade debits cash correctly ===")
    challenge.clear_challenge()
    cfg = challenge.set_challenge(date.today().isoformat(), (date.today() + timedelta(days=30)).isoformat(),
                                   1000, 2000, "shares", risk_pct_per_trade=3.0)
    sizing = challenge.suggest_position_size(callout, cfg, current_balance=1000)
    trade = challenge.add_trade("TEST", "swing", sizing)
    summary = challenge.get_summary()
    print("cash:", summary["cash_balance"], "equity:", summary["equity"])
    assert summary["cash_balance"] == round(1000 - trade["cost"], 2)
    assert summary["equity"] == 1000.0  # cash + open cost basis should equal starting budget before anything resolves
    assert len(summary["open_positions"]) == 1 and len(summary["closed_positions"]) == 0

    print("\n=== 5. check_open_positions resolves to TARGET correctly and computes P&L ===")
    challenge.fetch_daily = lambda ticker, period=None: flat_then_move_df(jump_high=108, jump_low=105, jump_close=107)
    trades, newly_closed = challenge.check_open_positions()
    print(trades[0])
    assert len(newly_closed) == 1 and newly_closed[0]["id"] == trades[0]["id"]
    assert trades[0]["status"] == "closed"
    assert trades[0]["exit_reason"] == "target"
    assert trades[0]["exit_price"] == 106.0  # exits AT the target level, not the overshoot close
    expected_pnl = trades[0]["qty"] * 106.0 - trades[0]["cost"]
    summary = challenge.get_summary()
    assert abs(summary["realized_pnl"] - expected_pnl) < 1e-6
    print("realized P&L matches expected:", summary["realized_pnl"])

    print("\n=== 6. check_open_positions resolves to STOP when both levels are touched the same window (conservative) ===")
    challenge.clear_challenge()
    cfg = challenge.set_challenge(date.today().isoformat(), (date.today() + timedelta(days=30)).isoformat(),
                                   1000, 2000, "shares", risk_pct_per_trade=3.0)
    sizing = challenge.suggest_position_size(callout, cfg, current_balance=1000)
    challenge.add_trade("TEST", "swing", sizing)
    # both stop (97) and target (106) crossed within the same forward window
    challenge.fetch_daily = lambda ticker, period=None: flat_then_move_df(jump_high=110, jump_low=90, jump_close=100)
    trades, _ = challenge.check_open_positions()
    print(trades[0]["exit_reason"], trades[0]["exit_price"])
    assert trades[0]["exit_reason"] == "stop"
    assert trades[0]["exit_price"] == 97.0

    print("\n=== 7. Time-stop closes a position that never hits either level ===")
    challenge.clear_challenge()
    cfg = challenge.set_challenge(date.today().isoformat(), (date.today() + timedelta(days=30)).isoformat(),
                                   1000, 2000, "shares", risk_pct_per_trade=3.0)
    sizing = challenge.suggest_position_size(callout, cfg, current_balance=1000)
    trade = challenge.add_trade("TEST", "swing", sizing)
    trade["max_hold_days"] = 2  # force a short hold window for the test instead of waiting out the real default
    challenge._save_json(challenge.TRADES_PATH, [trade])
    # entry_date/entry_time backdated so "days held" exceeds max_hold_days immediately
    trades = challenge._load_json(challenge.TRADES_PATH, [])
    trades[0]["entry_date"] = (date.today() - timedelta(days=5)).isoformat()
    trades[0]["entry_time"] = (datetime.now() - timedelta(days=5)).isoformat(timespec="seconds")
    challenge._save_json(challenge.TRADES_PATH, trades)
    challenge.fetch_daily = lambda ticker, period=None: flat_then_move_df()  # never reaches target/stop
    trades, _ = challenge.check_open_positions()
    print(trades[0]["exit_reason"])
    assert trades[0]["exit_reason"] == "time_stop"

    print("\n=== 8. Option exit is Black-Scholes re-priced, not a stale static number ===")
    challenge.clear_challenge()
    cfg = challenge.set_challenge(date.today().isoformat(), (date.today() + timedelta(days=30)).isoformat(),
                                   5000, 8000, "option", risk_pct_per_trade=5.0)
    callout2 = make_shares_callout()
    sizing = challenge.suggest_position_size(callout2, cfg, current_balance=5000)
    trade = challenge.add_trade("TEST", "intraday", sizing)
    challenge.fetch_intraday = lambda ticker, interval=None, period=None: flat_then_move_intraday_df(jump_high=108, jump_low=105, jump_close=107)
    trades, newly_closed = challenge.check_open_positions()
    print(trades[0])
    assert trades[0]["status"] == "closed"
    assert trades[0]["exit_price"] > sizing["entry_price"], "underlying rallied to target -- option should be worth more at exit"
    assert len(newly_closed) == 1

    print("\n=== 9. Intraday positions resolve SAME DAY (the bug this covers: entry-day bars used to be excluded entirely) ===")
    challenge.clear_challenge()
    cfg = challenge.set_challenge(date.today().isoformat(), (date.today() + timedelta(days=30)).isoformat(),
                                   5000, 8000, "option", risk_pct_per_trade=5.0)
    trade = challenge.add_trade("TEST", "intraday", sizing)
    # bars start ~1 minute after entry_time (set just now by add_trade) -- same calendar day
    challenge.fetch_intraday = lambda ticker, interval=None, period=None: flat_then_move_intraday_df(jump_high=108, jump_low=105, jump_close=107)
    trades, newly_closed = challenge.check_open_positions()
    assert trades[0]["status"] == "closed", "same-day intraday resolution should not require waiting for a new calendar day"
    assert len(newly_closed) == 1

    print("\n=== 10. Intraday time-stop fires on elapsed wall-clock minutes, not calendar days ===")
    challenge.clear_challenge()
    cfg = challenge.set_challenge(date.today().isoformat(), (date.today() + timedelta(days=30)).isoformat(),
                                   5000, 8000, "option", risk_pct_per_trade=5.0)
    trade = challenge.add_trade("TEST", "intraday", sizing)
    trade["entry_time"] = (datetime.now() - timedelta(hours=4)).isoformat(timespec="seconds")  # older than INTRADAY_MAX_HOLD_BARS*15min
    challenge._save_json(challenge.TRADES_PATH, [trade])
    challenge.fetch_intraday = lambda ticker, interval=None, period=None: flat_then_move_intraday_df()  # never hits target/stop
    trades, newly_closed = challenge.check_open_positions()
    print(trades[0]["exit_reason"])
    assert trades[0]["exit_reason"] == "time_stop"
    assert len(newly_closed) == 1

    challenge.clear_challenge()
    print("\n=== CHALLENGE VALIDATION: PASSED ===")


if __name__ == "__main__":
    main()
