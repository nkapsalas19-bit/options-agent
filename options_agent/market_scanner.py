"""
Market-wide scanning agent. Runs scan_universe() on a poll interval, writes
the latest results to scanner_results.json (the webapp's /api/scanner route
reads this file -- it does not run the scan itself, since sweeping hundreds
of tickers inside a web request would block/timeout), and emails a summary
the first time a callout crosses config.ALERT_MIN_CONFIDENCE each day.

Run this as its own long-lived process, separate from webapp/app.py:
    python market_scanner.py

Honest note on cadence: free data/news sources don't push true per-second
updates. Each cycle's actual wall-clock duration is printed -- if it's
longer than config.SCAN_INTERVAL_SECONDS, the loop just runs back-to-back
(it won't overlap itself, but it also won't sleep negative time).
"""
import json
import os
import time
from datetime import datetime, date

import config
from universe import get_scan_universe
from scanner import scan_universe
from alerts import send_email_alert
from push import send_push
import challenge

RESULTS_PATH = os.path.join(os.path.dirname(__file__), "scanner_results.json")
ALERTED_PATH = os.path.join(os.path.dirname(__file__), "alerted_callouts.json")
CHALLENGE_ALERTED_PATH = os.path.join(os.path.dirname(__file__), "challenge_alerted_callouts.json")


def _load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def _save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def _alert_key(c):
    # one alert per ticker+timeframe+direction per calendar day, so a
    # standing setup doesn't re-email every scan cycle
    return f"{c['ticker']}_{c['timeframe']}_{c['direction']}_{date.today().isoformat()}"


def _format_alert(c):
    subject = f"[Scanner] {c['direction']} {c['ticker']} ({c['timeframe']}) -- confidence {c['confidence_score']}"
    plan = c["exit_plan"]
    shares = plan["shares"]
    option = plan.get("option")

    exit_lines = [
        f"  Shares -- stop ${shares['stop_loss']} / target ${shares['profit_target']} "
        f"(reward:risk {shares['reward_risk_ratio']}:1). {shares['basis']}.",
        f"  Time-stop: {plan['time_stop']}",
        f"  Invalidation: {plan['invalidation_rule']}",
    ]
    if option:
        exit_lines.insert(1, f"  Option alt ({c['option_alt']['type']} ${c['option_alt']['strike']}, "
                              f"{c['option_alt']['dte_days']}d) -- stop ${option['stop_loss']} / "
                              f"target ${option['profit_target']}. {option['basis']}.")

    body = (
        f"{c['suggested_action']} on {c['ticker']}\n"
        f"Timeframe: {c['timeframe']}   Confidence score: {c['confidence_score']}/100\n"
        f"Entry (spot): ${c['spot']}\n\n"
        "Why:\n- " + "\n- ".join(c["reasons"]) + "\n\n"
        "Exit plan:\n" + "\n".join(exit_lines) + "\n\n"
        "This is a rule-based signal score, not a win-rate guarantee -- verify "
        "before trading, especially real bid/ask spread and options theta decay.\n"
        f"As of {c['as_of']}"
    )
    return subject, body


def _format_close_alert(t):
    pnl = t["proceeds"] - t["cost"]
    sign = "+" if pnl >= 0 else "-"
    reason_label = {"target": "hit its profit target", "stop": "hit its stop-loss",
                    "time_stop": "timed out (hit its max hold window) and was closed at the last price"}.get(
        t["exit_reason"], t["exit_reason"])
    subject = f"[Challenge] {t['ticker']} closed -- {sign}${abs(pnl):.2f} ({t['exit_reason']})"
    detail = (f"{t['qty']} {'shares' if t['instrument'] == 'shares' else 'contract(s)'} of "
              f"{t['ticker']}" + (f" ({t['option_type']} ${t['strike']} exp {t['expiration_date']})"
                                   if t["instrument"] == "option" else ""))
    body = (
        f"Challenge position {reason_label}.\n\n"
        f"{detail}\n"
        f"Entered {t['entry_date']} @ ${t['entry_price']}\n"
        f"Exited  {t['exit_date']} @ ${t['exit_price']}\n\n"
        f"P&L: {sign}${abs(pnl):.2f}  (cost ${t['cost']} -> proceeds ${t['proceeds']})\n\n"
        "This was resolved automatically against historical/delayed bar data, not a live "
        "broker fill -- if you're actually holding this trade, go check your real position; "
        "this alert is a reminder to act, not a confirmation that anything sold."
    )
    return subject, body


def run_once(tickers=None):
    """tickers, if given, overrides config-derived universe for just this run
    (e.g. the dashboard's "Scan These Tickers" picker) without touching
    config.SCANNER_WATCHLIST or requiring a redeploy."""
    tickers = tickers if tickers is not None else get_scan_universe()
    start = time.time()
    callouts, near_misses = scan_universe(tickers)
    elapsed = time.time() - start
    print(f"[scanner] swept {len(tickers)} tickers in {elapsed:.1f}s -> {len(callouts)} callouts >= {config.MIN_CONFIDENCE_SCORE}"
          f" ({len(near_misses)} near-misses)")

    _save_json(RESULTS_PATH, {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "universe_size": len(tickers),
        "tickers_scanned": tickers,
        "cycle_seconds": round(elapsed, 1),
        "callouts": callouts,
        "near_misses": near_misses,
        "min_confidence_score": config.MIN_CONFIDENCE_SCORE,
    })

    alerted = set(_load_json(ALERTED_PATH, []))
    for c in callouts:
        if c["confidence_score"] < config.ALERT_MIN_CONFIDENCE:
            continue
        key = _alert_key(c)
        if key in alerted:
            continue
        subject, body = _format_alert(c)
        send_email_alert(subject, body)
        send_push(subject, f"{c['suggested_action']} -- confidence {c['confidence_score']}/100. "
                            f"Open the dashboard for the full reasoning and exit plan.",
                  priority="default", tags=["chart_with_upwards_trend" if c["direction"] == "BULLISH" else "chart_with_downwards_trend"])
        alerted.add(key)

    _save_json(ALERTED_PATH, sorted(alerted))

    cfg = challenge.get_challenge()
    if cfg:
        try:
            summary = challenge.get_summary()
            challenge_alerted = set(_load_json(CHALLENGE_ALERTED_PATH, []))
            for c in callouts:
                sizing = challenge.suggest_position_size(c, cfg, summary["cash_balance"])
                if not sizing:
                    continue
                key = _alert_key(c)
                if key in challenge_alerted:
                    continue
                detail = (f"{sizing['qty']} shares" if sizing["instrument"] == "shares"
                          else f"{sizing['qty']} {sizing['option_type']} contract(s) ${sizing['strike']} "
                               f"exp {sizing['expiration_date']}")
                send_push(
                    f"Challenge trade: {c['direction']} {c['ticker']}",
                    f"{detail} @ ${sizing['entry_price']:.2f} -- cost ${sizing['cost']:.2f}, "
                    f"risking ${sizing['risk_dollars']:.2f}. Confidence {c['confidence_score']}/100. "
                    "Open the dashboard's Challenge panel to add it.",
                    priority="high", tags=["moneybag"],
                )
                challenge_alerted.add(key)
            _save_json(CHALLENGE_ALERTED_PATH, sorted(challenge_alerted))
        except Exception as e:
            print(f"[scanner] challenge recommendation push failed: {e}")

        try:
            _, newly_closed = challenge.check_open_positions()
            for t in newly_closed:
                subject, body = _format_close_alert(t)
                send_email_alert(subject, body)
                pnl = t["proceeds"] - t["cost"]
                send_push(subject, f"{'+' if pnl >= 0 else '-'}${abs(pnl):.2f} -- go check your real "
                                    "position if you're shadowing this trade.",
                          priority="high", tags=["moneybag"])
        except Exception as e:
            print(f"[scanner] challenge position check failed: {e}")

    return callouts


def main_loop(poll_seconds=None, max_iterations=None):
    poll_seconds = poll_seconds if poll_seconds is not None else config.SCAN_INTERVAL_SECONDS
    i = 0
    while max_iterations is None or i < max_iterations:
        run_once()
        i += 1
        if max_iterations is None or i < max_iterations:
            time.sleep(poll_seconds)


if __name__ == "__main__":
    main_loop(max_iterations=1)  # single sweep by default; drop max_iterations for continuous polling
