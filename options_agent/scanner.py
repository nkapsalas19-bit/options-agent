"""
Market-wide scanner: combines the existing rule-based technical strategies
(strategies.py) with free news sentiment (news.py) across a configurable
ticker universe (universe.py) and two timeframes, producing ranked
"callouts" -- candidate trades with a transparent, explainable confidence
score, NOT a win-rate promise. Every point in the score traces back to a
specific rule that fired; there's no black-box model here.

Two timeframes are checked per ticker:
  "intraday" -- 15-minute bars, short-dated options (calls/puts), for
                same-day / few-day moves.
  "swing"    -- daily bars, primarily a "buy/short shares" suggestion, with
                a longer-dated option alternative also computed.

Scoring (0-100, both components additive, see _technical_score/_news_score):
  - up to 60 pts from technical agreement: how many of the timeframe's
    strategies fired the same direction, RSI confirmation, and volume
    confirmation.
  - up to +/-40 pts from news sentiment: bonus if recent headline sentiment
    (VADER, see news.py) agrees with the technical direction, penalty if it
    conflicts. No headlines found = 0 contribution either way.
Callouts below config.MIN_CONFIDENCE_SCORE are discarded entirely.
"""
from datetime import datetime

import numpy as np

import config
from data_fetcher import fetch_daily, fetch_intraday
from strategies import STRATEGY_FUNCS
from news import get_news_sentiment
from options_pricing import bs_price, select_strike

TIMEFRAME_STRATEGIES = {
    "swing": {
        "interval": "1d",
        "period": "6mo",
        "strategies": ["ma_rsi", "bb_squeeze_breakout"],
        "target_dte": config.SWING_TARGET_DTE_DAYS,
        "instrument": "shares",
    },
    "intraday": {
        "interval": "15m",
        "period": "5d",
        "strategies": ["ma_rsi", "bb_squeeze_breakout"],
        "target_dte": config.TARGET_DTE_DAYS,
        "instrument": "option",
    },
}


def _fetch(ticker, tf_cfg):
    if tf_cfg["interval"] == "1d":
        return fetch_daily(ticker, period=tf_cfg["period"])
    return fetch_intraday(ticker, interval=tf_cfg["interval"], period=tf_cfg["period"])


def _technical_score(df, strategy_names):
    """Runs each configured strategy on df, returns (direction, score_0_60, reasons)."""
    votes = []
    reasons = []
    latest_rsi = None

    for name in strategy_names:
        sig_df = STRATEGY_FUNCS[name](df, config.STRATEGIES[name])
        latest = sig_df.iloc[-1]
        sig = latest.get("signal", 0)
        if sig != 0:
            votes.append(sig)
            reasons.append(f"{name} fired {'bullish' if sig == 1 else 'bearish'}")
        if "rsi" in sig_df.columns and latest_rsi is None:
            latest_rsi = sig_df["rsi"].iloc[-1]

    if not votes:
        return 0, 0, []

    net = sum(votes)
    if net == 0:
        return 0, 0, ["conflicting signals across strategies -- skipped"]

    direction = 1 if net > 0 else -1
    agreeing = sum(1 for v in votes if v == direction)
    score = min(agreeing * 20, 40)

    if latest_rsi is not None and not np.isnan(latest_rsi):
        if (direction == 1 and latest_rsi < 40) or (direction == -1 and latest_rsi > 60):
            score += 10
            reasons.append(f"RSI confirms ({latest_rsi:.0f})")

    if len(df) >= 20:
        vol_avg = df["volume"].rolling(20).mean().iloc[-1]
        vol_latest = df["volume"].iloc[-1]
        if vol_avg and not np.isnan(vol_avg) and vol_latest > vol_avg * 1.3:
            score += 10
            reasons.append("volume above 20-bar average")

    return direction, min(score, 60), reasons


def _news_score(ticker, direction):
    sentiment, headlines = get_news_sentiment(ticker, config.NEWS_LOOKBACK_HOURS)
    if sentiment is None or direction == 0:
        return 0, []

    aligned = (sentiment > 0.05 and direction == 1) or (sentiment < -0.05 and direction == -1)
    conflicting = (sentiment > 0.05 and direction == -1) or (sentiment < -0.05 and direction == 1)

    if aligned:
        score = min(abs(sentiment) * 40, 40)
        headline_note = f' -- "{headlines[0]}"' if headlines else ""
        return score, [f"news sentiment aligned ({sentiment:+.2f}){headline_note}"]
    if conflicting:
        score = -min(abs(sentiment) * 30, 30)
        return score, [f"news sentiment conflicts ({sentiment:+.2f}) -- confidence reduced"]
    return 0, []


def scan_ticker(ticker, timeframe_name):
    tf_cfg = TIMEFRAME_STRATEGIES[timeframe_name]
    try:
        df = _fetch(ticker, tf_cfg)
    except Exception:
        return None

    if len(df) < 30:
        return None

    direction, tech_score, tech_reasons = _technical_score(df, tf_cfg["strategies"])
    if direction == 0:
        return None

    news_score, news_reasons = _news_score(ticker, direction)
    confidence = max(0.0, min(100.0, tech_score + news_score))
    if confidence < config.MIN_CONFIDENCE_SCORE:
        return None

    spot = float(df["close"].iloc[-1])
    option_type = "call" if direction == 1 else "put"
    strike = select_strike(spot, config.STRIKE_OFFSET_PCT, option_type)
    iv = config.DEFAULT_IV.get(ticker, config.DEFAULT_IV_FALLBACK)
    dte = tf_cfg["target_dte"]
    est_option_price = bs_price(spot, strike, dte, iv, config.RISK_FREE_RATE, option_type)

    suggested_action = "BUY SHARES" if tf_cfg["instrument"] == "shares" and direction == 1 else (
        "SHORT / SELL SHARES" if tf_cfg["instrument"] == "shares" else f"BUY {option_type.upper()}"
    )

    return {
        "ticker": ticker,
        "timeframe": timeframe_name,
        "direction": "BULLISH" if direction == 1 else "BEARISH",
        "suggested_action": suggested_action,
        "instrument_primary": tf_cfg["instrument"],
        "option_alt": {
            "type": option_type, "strike": strike, "dte_days": dte,
            "est_price": round(est_option_price, 2), "iv_assumed": iv,
        },
        "confidence_score": round(confidence, 1),
        "spot": round(spot, 2),
        "reasons": tech_reasons + news_reasons,
        "as_of": datetime.now().isoformat(timespec="seconds"),
    }


def scan_universe(tickers, timeframe_names=None):
    timeframe_names = timeframe_names or list(TIMEFRAME_STRATEGIES.keys())
    callouts = []
    for ticker in tickers:
        for tf_name in timeframe_names:
            try:
                result = scan_ticker(ticker, tf_name)
            except Exception as e:
                print(f"[scanner] {ticker}/{tf_name} failed: {e}")
                result = None
            if result:
                callouts.append(result)
    callouts.sort(key=lambda c: c["confidence_score"], reverse=True)
    return callouts
