"""
Market-wide scanner: combines the existing rule-based technical strategies
(strategies.py) with free news sentiment (news.py) across a configurable
ticker universe (universe.py) and two timeframes, producing ranked
"callouts" -- candidate trades with a transparent, explainable confidence
score AND a concrete exit plan. Every point in the score and every exit
level traces back to a specific rule and a real number computed from the
data -- there's no black-box model here, and it is NOT a win-rate promise.

Two timeframes are checked per ticker:
  "intraday" -- 15-minute bars, short-dated options (calls/puts), for
                same-day / few-day moves.
  "swing"    -- daily bars, primarily a "buy/short shares" suggestion, with
                a longer-dated option alternative also computed.

Scoring (0-100, both components additive, see _technical_score/_news_score):
  - up to 60 pts from technical agreement: how many of the timeframe's
    strategies fired the same direction, RSI confirmation, volume
    confirmation, and (intraday only) trend-filter alignment.
  - up to +/-40 pts from news sentiment: bonus if recent headline sentiment
    (VADER, see news.py) agrees with the technical direction, penalty if it
    conflicts. No headlines found = 0 contribution either way.

Accuracy lever: on the swing timeframe, a signal that fights the longer-term
trend (config.TREND_FILTER_PERIOD-bar SMA) is discarded outright, not just
scored lower -- counter-trend swing trades are the single most common source
of losing "textbook" setups. Callouts below config.MIN_CONFIDENCE_SCORE are
also discarded entirely.
"""
from datetime import datetime, timedelta

import numpy as np

import config
from data_fetcher import fetch_daily, fetch_intraday
from strategies import STRATEGY_FUNCS
from indicators import atr as atr_indicator
from news import get_news_sentiment
from options_pricing import bs_price, select_strike

TIMEFRAME_STRATEGIES = {
    "swing": {
        "interval": "1d",
        "period": "1y",
        "strategies": ["ma_rsi", "bb_squeeze_breakout"],
        "target_dte": config.SWING_TARGET_DTE_DAYS,
        "instrument": "shares",
        "trend_filter_period": config.TREND_FILTER_PERIOD,
        "trend_filter_hard": True,
    },
    "intraday": {
        "interval": "15m",
        "period": "5d",
        "strategies": ["ma_rsi", "bb_squeeze_breakout"],
        "target_dte": config.TARGET_DTE_DAYS,
        "instrument": "option",
        "trend_filter_period": config.INTRADAY_TREND_FILTER_PERIOD,
        "trend_filter_hard": False,
    },
}


def _fetch(ticker, tf_cfg):
    if tf_cfg["interval"] == "1d":
        return fetch_daily(ticker, period=tf_cfg["period"])
    return fetch_intraday(ticker, interval=tf_cfg["interval"], period=tf_cfg["period"])


def _describe_ma_rsi(sig_df, direction):
    latest = sig_df.iloc[-1]
    fast, slow, rsi_val = latest["fast_ma"], latest["slow_ma"], latest.get("rsi")
    when = sig_df.index[-1]
    when_str = when.strftime("%Y-%m-%d %H:%M") if hasattr(when, "strftime") else str(when)
    verb = "crossed above" if direction == 1 else "crossed below"
    fast_n = config.STRATEGIES["ma_rsi"]["fast_ma"]
    slow_n = config.STRATEGIES["ma_rsi"]["slow_ma"]
    reasons = [f"MA/RSI: {fast_n}-period MA (${fast:.2f}) {verb} {slow_n}-period MA (${slow:.2f}) on {when_str}"]
    if rsi_val is not None and not np.isnan(rsi_val):
        note = "confirms room to run" if (direction == 1 and rsi_val < 65) or (direction == -1 and rsi_val > 35) else "already stretched -- weaker confirmation"
        reasons.append(f"RSI at {rsi_val:.0f} ({note})")
    return reasons


def _describe_bb_breakout(sig_df, direction):
    latest = sig_df.iloc[-1]
    close, band = latest["close"], (latest["bb_upper"] if direction == 1 else latest["bb_lower"])
    bw = latest.get("bandwidth")
    side = "above upper band" if direction == 1 else "below lower band"
    reason = f"Bollinger squeeze breakout: price (${close:.2f}) closed {side} (${band:.2f}) after a low-volatility squeeze"
    if bw is not None and not np.isnan(bw):
        reason += f" (bandwidth {bw*100:.1f}% of price)"
    return [reason]


_DESCRIBERS = {"ma_rsi": _describe_ma_rsi, "bb_squeeze_breakout": _describe_bb_breakout}


def _trend_filter(df, direction, period):
    """Compares price against a longer SMA to check the signal isn't fighting
    the broader trend. Returns (aligned: bool or None if not enough data, reason)."""
    if len(df) < period:
        return None, []
    sma_val = df["close"].rolling(period).mean().iloc[-1]
    if np.isnan(sma_val):
        return None, []
    price = df["close"].iloc[-1]
    trend_up = price > sma_val
    aligned = (direction == 1 and trend_up) or (direction == -1 and not trend_up)
    pct_diff = (price - sma_val) / sma_val * 100
    reason = (f"trend filter: price (${price:.2f}) is {'above' if trend_up else 'below'} the "
              f"{period}-bar SMA (${sma_val:.2f}, {pct_diff:+.1f}%) -- "
              f"{'aligned with' if aligned else 'AGAINST'} the {'bullish' if direction == 1 else 'bearish'} signal")
    return aligned, [reason]


def _technical_score(df, strategy_names, trend_filter_period=None, trend_filter_hard=False):
    """Runs each configured strategy on df, returns (direction, score_0_60, reasons, discarded)."""
    votes = []
    reasons = []
    latest_rsi = None
    fired_names = []

    for name in strategy_names:
        sig_df = STRATEGY_FUNCS[name](df, config.STRATEGIES[name])
        latest = sig_df.iloc[-1]
        sig = latest.get("signal", 0)
        if sig != 0:
            votes.append(sig)
            fired_names.append((name, sig, sig_df))
        if "rsi" in sig_df.columns and latest_rsi is None:
            latest_rsi = sig_df["rsi"].iloc[-1]

    if not votes:
        return 0, 0, [], False

    net = sum(votes)
    if net == 0:
        return 0, 0, ["conflicting signals across strategies -- skipped"], False

    direction = 1 if net > 0 else -1
    agreeing = [(name, sig_df) for name, sig, sig_df in fired_names if sig == direction]
    score = min(len(agreeing) * 20, 40)

    for name, sig_df in agreeing:
        reasons.extend(_DESCRIBERS[name](sig_df, direction))

    if latest_rsi is not None and not np.isnan(latest_rsi):
        if (direction == 1 and latest_rsi < 40) or (direction == -1 and latest_rsi > 60):
            score += 10
            reasons.append(f"RSI confirms extreme ({latest_rsi:.0f})")

    if len(df) >= 20:
        vol_avg = df["volume"].rolling(20).mean().iloc[-1]
        vol_latest = df["volume"].iloc[-1]
        if vol_avg and not np.isnan(vol_avg) and vol_latest > vol_avg * 1.3:
            score += 10
            reasons.append(f"volume {vol_latest/vol_avg:.1f}x its 20-bar average ({vol_latest:,.0f} vs {vol_avg:,.0f})")

    if trend_filter_period:
        aligned, trend_reasons = _trend_filter(df, direction, trend_filter_period)
        # aligned can be a numpy.bool_ (from a pandas comparison) or None (not enough
        # data yet) -- compare by truthiness/None, never "is True"/"is False" identity,
        # since numpy.bool_(True) is not the Python singleton True.
        if aligned is None:
            pass
        elif not aligned:
            if trend_filter_hard:
                return direction, 0, trend_reasons + ["discarded: counter-trend signal on the swing timeframe"], True
            reasons.extend(trend_reasons)  # soft warning only (intraday) -- no score change
        else:
            score += 10
            reasons.extend(trend_reasons)

    return direction, min(score, 60), reasons, False


def _news_score(ticker, direction):
    sentiment, headlines = get_news_sentiment(ticker, config.NEWS_LOOKBACK_HOURS)
    if sentiment is None or direction == 0:
        return 0, []

    aligned = (sentiment > 0.05 and direction == 1) or (sentiment < -0.05 and direction == -1)
    conflicting = (sentiment > 0.05 and direction == -1) or (sentiment < -0.05 and direction == 1)
    headline_note = f' -- e.g. "{headlines[0]}"' if headlines else ""
    count_note = f"{len(headlines)} headline{'s' if len(headlines) != 1 else ''} in the last {config.NEWS_LOOKBACK_HOURS}h"

    if aligned:
        score = min(abs(sentiment) * 40, 40)
        return score, [f"news sentiment aligned ({sentiment:+.2f} avg over {count_note}){headline_note}"]
    if conflicting:
        score = -min(abs(sentiment) * 30, 30)
        return score, [f"news sentiment CONFLICTS ({sentiment:+.2f} avg over {count_note}) -- confidence reduced{headline_note}"]
    return 0, [f"news sentiment neutral ({sentiment:+.2f} avg over {count_note})"]


def _build_exit_plan(direction, instrument, spot, df, tf_cfg, entry_option_price=None, option_type=None, dte=None):
    """Produces concrete, numeric exit rules -- not just a score. Shares use
    ATR-scaled stop/target so the levels reflect this ticker's actual recent
    volatility; options use the config's percent-of-premium rule (theta decay
    makes a wide underlying-based stop meaningless on a short-dated contract).
    Every plan also has a time-stop and a condition-based invalidation rule,
    since "the setup stopped being true" can happen before either price level hits."""
    atr_series = atr_indicator(df, config.ATR_PERIOD)
    atr_val = atr_series.iloc[-1] if len(atr_series) else np.nan
    if np.isnan(atr_val) or atr_val <= 0:
        atr_val = spot * 0.02  # fallback: ~2% of price if ATR isn't computable yet (short history)

    sign = 1 if direction == 1 else -1
    shares_stop = spot - sign * config.ATR_STOP_MULT * atr_val
    shares_target = spot + sign * config.ATR_TARGET_MULT * atr_val
    risk_per_share = abs(spot - shares_stop)
    reward_per_share = abs(shares_target - spot)
    rr_ratio = reward_per_share / risk_per_share if risk_per_share > 0 else None

    plan = {
        "entry_price": round(spot, 2),
        "shares": {
            "stop_loss": round(shares_stop, 2),
            "profit_target": round(shares_target, 2),
            "risk_per_share": round(risk_per_share, 2),
            "reward_per_share": round(reward_per_share, 2),
            "reward_risk_ratio": round(rr_ratio, 2) if rr_ratio else None,
            "basis": f"stop = entry {'-' if direction == 1 else '+'} {config.ATR_STOP_MULT}x ATR(${atr_val:.2f}); "
                     f"target = entry {'+' if direction == 1 else '-'} {config.ATR_TARGET_MULT}x ATR",
        },
        "time_stop": (
            f"re-evaluate after {config.SWING_MAX_HOLD_DAYS} trading days if neither level hits"
            if instrument == "shares" else
            f"close the position after {config.INTRADAY_MAX_HOLD_BARS} bars "
            f"(~{config.INTRADAY_MAX_HOLD_BARS * 15} min on 15m bars) if neither level hits, "
            "since theta decay accelerates against a stale short-dated option regardless of price"
        ),
        "invalidation_rule": (
            f"exit early if price closes back {'below' if direction == 1 else 'above'} the level that "
            f"triggered entry (fast/slow MA re-cross or back inside the Bollinger band) -- the technical "
            f"thesis is no longer true even if the stop hasn't been hit yet"
        ),
    }

    if entry_option_price is not None:
        target_price = entry_option_price * (1 + config.PROFIT_TARGET_PCT)
        stop_price = entry_option_price * (1 - config.STOP_LOSS_PCT)
        plan["option"] = {
            "entry_price": round(entry_option_price, 2),
            "profit_target": round(target_price, 2),
            "stop_loss": round(stop_price, 2),
            "basis": f"+{config.PROFIT_TARGET_PCT*100:.0f}% / -{config.STOP_LOSS_PCT*100:.0f}% of option "
                     f"premium (not the underlying) -- theta decay on a {dte}-day contract makes an "
                     f"underlying-price-based stop unreliable for the option's actual P&L",
        }

    return plan


def scan_ticker(ticker, timeframe_name):
    tf_cfg = TIMEFRAME_STRATEGIES[timeframe_name]
    try:
        df = _fetch(ticker, tf_cfg)
    except Exception:
        return None

    if len(df) < 30:
        return None

    direction, tech_score, tech_reasons, discarded = _technical_score(
        df, tf_cfg["strategies"],
        trend_filter_period=tf_cfg["trend_filter_period"],
        trend_filter_hard=tf_cfg["trend_filter_hard"],
    )
    if direction == 0 or discarded:
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

    exit_plan = _build_exit_plan(
        direction, tf_cfg["instrument"], spot, df, tf_cfg,
        entry_option_price=est_option_price, option_type=option_type, dte=dte,
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
        "exit_plan": exit_plan,
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
