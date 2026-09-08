"""
Market-wide scanner: combines the existing rule-based technical strategies
(strategies.py) with relative strength, multi-timeframe confluence, free news
sentiment, and event risk (earnings) across a configurable ticker universe
(universe.py) and two timeframes, producing ranked "callouts" -- candidate
trades with a transparent, explainable confidence score AND a concrete exit
plan. Every point in the score and every exit level traces back to a specific
rule and a real number computed from the data -- there's no black-box model
here, and it is NOT a win-rate promise.

Two timeframes are checked per ticker:
  "intraday" -- 15-minute bars, short-dated options (calls/puts), for
                same-day / few-day moves.
  "swing"    -- daily bars, primarily a "buy/short shares" suggestion, with
                a longer-dated option alternative also computed.

Scoring is split into two tiers on purpose (see config.py's point-budget
section), because one of those tiers can be validated against history and
the other can't:

  BACKTESTABLE tier (see backtestable_score() below, up to 80 pts) --
  strategy agreement, RSI, volume, the trend filter, multi-timeframe
  confluence, and relative strength. All of it is computable from historical
  OHLCV alone, which is exactly what scanner_backtest.py replays bar-by-bar
  (importing this same function) to report an ACTUAL historical win rate,
  profit factor, and score-calibration table -- not just a plausible-sounding
  number. Run `python scanner_backtest.py` to see it.

  LIVE-ONLY tier (news sentiment, earnings-date risk, sector confirmation)
  -- these can't be backtested with free data (no historical news archive,
  and simulating point-in-time earnings dates for hundreds of tickers across
  years is out of scope here), so they're layered on top of the backtested
  score for live callouts only. This means a live confidence score and the
  backtest's tech_score are related but not identical -- documented, not
  hidden.

Accuracy levers, in order of how much they matter:
  1. Trend filter: a swing signal that fights its own 50-bar SMA trend is
     discarded outright, not just scored lower -- counter-trend swing trades
     are the single most common source of losing "textbook" setups.
  2. Relative strength: is this ticker actually leading/lagging the broader
     market (SPY) over the last N days, or is the signal happening in a name
     nobody's trading?
  3. Multi-timeframe confluence: for intraday callouts, does the DAILY trend
     (not just the 15-minute one) agree with the trade direction?
  4. Earnings-date risk: an option suggestion inside an earnings window gets
     flagged (IV crush risk), not silently ignored.
Callouts below config.MIN_CONFIDENCE_SCORE are discarded entirely.
"""
from datetime import datetime, timedelta

import numpy as np

import config
from data_fetcher import fetch_daily, fetch_intraday
from strategies import STRATEGY_FUNCS
from indicators import atr as atr_indicator
from news import get_news_sentiment
from fundamentals import days_until_earnings, get_sector_etf
from options_pricing import bs_price, select_strike

RECENT_SIGNAL_LOOKBACK_BARS = 5   # how many recent daily bars scan_universe's near-miss diagnostic checks back

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


def _describe_ma_rsi(sig_df, direction, idx=-1):
    latest = sig_df.iloc[idx]
    fast, slow, rsi_val = latest["fast_ma"], latest["slow_ma"], latest.get("rsi")
    when = sig_df.index[idx]
    when_str = when.strftime("%Y-%m-%d %H:%M") if hasattr(when, "strftime") else str(when)
    verb = "crossed above" if direction == 1 else "crossed below"
    fast_n = config.STRATEGIES["ma_rsi"]["fast_ma"]
    slow_n = config.STRATEGIES["ma_rsi"]["slow_ma"]
    reasons = [f"MA/RSI: {fast_n}-period MA (${fast:.2f}) {verb} {slow_n}-period MA (${slow:.2f}) on {when_str}"]
    if rsi_val is not None and not np.isnan(rsi_val):
        note = "confirms room to run" if (direction == 1 and rsi_val < 65) or (direction == -1 and rsi_val > 35) else "already stretched -- weaker confirmation"
        reasons.append(f"RSI at {rsi_val:.0f} ({note})")
    return reasons


def _describe_bb_breakout(sig_df, direction, idx=-1):
    latest = sig_df.iloc[idx]
    close, band = latest["close"], (latest["bb_upper"] if direction == 1 else latest["bb_lower"])
    bw = latest.get("bandwidth")
    side = "above upper band" if direction == 1 else "below lower band"
    reason = f"Bollinger squeeze breakout: price (${close:.2f}) closed {side} (${band:.2f}) after a low-volatility squeeze"
    if bw is not None and not np.isnan(bw):
        reason += f" (bandwidth {bw*100:.1f}% of price)"
    return [reason]


_DESCRIBERS = {"ma_rsi": _describe_ma_rsi, "bb_squeeze_breakout": _describe_bb_breakout}


def _trend_filter(df, direction, period, idx=-1, label="trend filter"):
    """Compares price against a longer SMA to check the signal isn't fighting
    the broader trend. Returns (aligned: bool or None if not enough data, reason).
    Rolling means are causal by construction, so computing them once on the
    full df and indexing at idx is identical to recomputing on a truncated
    df ending at idx -- this is what makes it safe to reuse in a backtest."""
    sma = df["close"].rolling(period).mean()
    sma_val = sma.iloc[idx]
    if np.isnan(sma_val):
        return None, []
    price = df["close"].iloc[idx]
    trend_up = price > sma_val
    aligned = (direction == 1 and trend_up) or (direction == -1 and not trend_up)
    pct_diff = (price - sma_val) / sma_val * 100
    reason = (f"{label}: price (${price:.2f}) is {'above' if trend_up else 'below'} the "
              f"{period}-bar SMA (${sma_val:.2f}, {pct_diff:+.1f}%) -- "
              f"{'aligned with' if aligned else 'AGAINST'} the {'bullish' if direction == 1 else 'bearish'} signal")
    return bool(aligned), [reason]


def relative_strength_excess(ticker_df, benchmark_df, idx=-1, lookback=None):
    """Excess return of ticker_df vs benchmark_df over `lookback` bars, ending
    at idx. Aligned by integer position, not date -- both series come from the
    same source/period/interval request so their trading calendars should
    already match; this is an approximation, not a guaranteed date join.
    Returns None if either series doesn't have enough history at idx."""
    lookback = lookback or config.RS_LOOKBACK_DAYS
    row_pos = idx if idx >= 0 else len(ticker_df) + idx
    if row_pos < lookback or benchmark_df is None or row_pos >= len(benchmark_df):
        return None

    t_now, t_then = ticker_df["close"].iloc[idx], ticker_df["close"].iloc[row_pos - lookback]
    b_now, b_then = benchmark_df["close"].iloc[idx], benchmark_df["close"].iloc[row_pos - lookback]
    if t_then <= 0 or b_then <= 0:
        return None
    return (t_now / t_then - 1) - (b_now / b_then - 1)


def _relative_strength_bonus(direction, rs_value):
    if rs_value is None:
        return 0, []
    pct = rs_value * 100
    if direction == 1 and rs_value >= config.RS_OUTPERFORM_THRESHOLD:
        return config.RS_BONUS, [f"relative strength: outperformed {config.RS_BENCHMARK} by {pct:+.1f}pp over "
                                  f"{config.RS_LOOKBACK_DAYS}d -- a market leader, not a laggard bouncing"]
    if direction == -1 and rs_value <= -config.RS_OUTPERFORM_THRESHOLD:
        return config.RS_BONUS, [f"relative strength: lagged {config.RS_BENCHMARK} by {pct:+.1f}pp over "
                                  f"{config.RS_LOOKBACK_DAYS}d -- confirms genuine weakness"]
    return 0, [f"relative strength: {pct:+.1f}pp vs {config.RS_BENCHMARK} over {config.RS_LOOKBACK_DAYS}d "
               f"-- not a strong confirmation either way"]


def _technical_score(df, strategy_names, trend_filter_period=None, trend_filter_hard=False, idx=-1, precomputed=None):
    """Runs each configured strategy on df, returns (direction, score_0_60, reasons, discarded).
    idx/precomputed let a caller (scanner_backtest.py) evaluate this at any
    historical bar without recomputing every rolling indicator from scratch
    each time -- see the module docstring for why that's still lookahead-safe."""
    votes = []
    reasons = []
    latest_rsi = None
    fired_names = []

    for name in strategy_names:
        sig_df = (precomputed or {}).get(name)
        if sig_df is None:
            sig_df = STRATEGY_FUNCS[name](df, config.STRATEGIES[name])
        latest = sig_df.iloc[idx]
        sig = latest.get("signal", 0)
        if sig != 0:
            votes.append(sig)
            fired_names.append((name, sig, sig_df))
        if "rsi" in sig_df.columns and latest_rsi is None:
            rsi_val = sig_df["rsi"].iloc[idx]
            if not np.isnan(rsi_val):
                latest_rsi = rsi_val

    if not votes:
        return 0, 0, [], False

    net = sum(votes)
    if net == 0:
        return 0, 0, ["conflicting signals across strategies -- skipped"], False

    direction = 1 if net > 0 else -1
    agreeing = [(name, sig_df) for name, sig, sig_df in fired_names if sig == direction]
    score = min(len(agreeing) * config.AGREEMENT_PTS_PER_STRATEGY, config.AGREEMENT_MAX)

    for name, sig_df in agreeing:
        reasons.extend(_DESCRIBERS[name](sig_df, direction, idx))

    if latest_rsi is not None:
        if (direction == 1 and latest_rsi < 40) or (direction == -1 and latest_rsi > 60):
            score += config.RSI_BONUS
            reasons.append(f"RSI confirms extreme ({latest_rsi:.0f})")

    vol_avg = df["volume"].rolling(20).mean().iloc[idx]
    vol_latest = df["volume"].iloc[idx]
    if not np.isnan(vol_avg) and vol_avg > 0 and vol_latest > vol_avg * 1.3:
        score += config.VOLUME_BONUS
        reasons.append(f"volume {vol_latest/vol_avg:.1f}x its 20-bar average ({vol_latest:,.0f} vs {vol_avg:,.0f})")

    if trend_filter_period:
        aligned, trend_reasons = _trend_filter(df, direction, trend_filter_period, idx=idx)
        # aligned is None (not enough data), or a plain bool -- never compare with
        # "is True"/"is False", since a numpy.bool_ from a pandas comparison is not
        # the Python singleton True/False.
        if aligned is None:
            pass
        elif not aligned:
            if trend_filter_hard:
                return direction, 0, trend_reasons + ["discarded: counter-trend signal on the swing timeframe"], True
            reasons.extend(trend_reasons)  # soft warning only (intraday) -- no score change
        else:
            score += config.TREND_FILTER_BONUS
            reasons.extend(trend_reasons)

    return direction, min(score, config.TECH_SCORE_MAX), reasons, False


def backtestable_score(df, strategy_names, trend_filter_period=None, trend_filter_hard=False,
                        idx=-1, precomputed=None, daily_df=None, mtf_idx=-1, rs_value=None):
    """Everything computable from OHLCV alone: _technical_score plus
    multi-timeframe confluence (if daily_df given -- intraday callouts only)
    plus relative strength (if rs_value given). This is the exact function
    scanner_backtest.py replays across history, so a live callout's
    "backtestable" component and the backtest's tech_score come from the
    same code path, not a re-implementation that could quietly drift."""
    direction, score, reasons, discarded = _technical_score(
        df, strategy_names, trend_filter_period, trend_filter_hard, idx=idx, precomputed=precomputed,
    )
    if direction == 0 or discarded:
        return direction, score, reasons, discarded

    if daily_df is not None:
        aligned, mtf_reasons = _trend_filter(daily_df, direction, config.TREND_FILTER_PERIOD, idx=mtf_idx,
                                              label="higher-timeframe (daily) trend")
        if aligned is None:
            pass
        elif aligned:
            score += config.MTF_CONFLUENCE_BONUS
            reasons.extend(mtf_reasons)
        else:
            reasons.extend(mtf_reasons)  # soft warning only -- intraday reversals can trade against the daily trend

    rs_bonus, rs_reasons = _relative_strength_bonus(direction, rs_value)
    score += rs_bonus
    reasons.extend(rs_reasons)

    return direction, min(score, config.BACKTESTABLE_SCORE_MAX), reasons, False


def _news_score(ticker, direction):
    sentiment, headlines = get_news_sentiment(ticker, config.NEWS_LOOKBACK_HOURS)
    if sentiment is None or direction == 0:
        return 0, []

    aligned = (sentiment > 0.05 and direction == 1) or (sentiment < -0.05 and direction == -1)
    conflicting = (sentiment > 0.05 and direction == -1) or (sentiment < -0.05 and direction == 1)
    headline_note = f' -- e.g. "{headlines[0]}"' if headlines else ""
    count_note = f"{len(headlines)} headline{'s' if len(headlines) != 1 else ''} in the last {config.NEWS_LOOKBACK_HOURS}h"

    if aligned:
        score = min(abs(sentiment) * config.NEWS_BONUS_MAX / 0.9, config.NEWS_BONUS_MAX)
        return score, [f"news sentiment aligned ({sentiment:+.2f} avg over {count_note}){headline_note}"]
    if conflicting:
        score = -min(abs(sentiment) * config.NEWS_PENALTY_MAX / 0.9, config.NEWS_PENALTY_MAX)
        return score, [f"news sentiment CONFLICTS ({sentiment:+.2f} avg over {count_note}) -- confidence reduced{headline_note}"]
    return 0, [f"news sentiment neutral ({sentiment:+.2f} avg over {count_note})"]


def _earnings_penalty(earnings_days, dte):
    if earnings_days is None:
        return 0, []
    if 0 <= earnings_days <= max(dte, config.EARNINGS_BLACKOUT_DAYS):
        return -config.EARNINGS_PENALTY, [f"EARNINGS in {earnings_days}d, inside the option's {dte}-day window -- "
                                           f"IV crush risk after the print; size down or skip the option leg"]
    return 0, []


def _risk_level(instrument, atr_pct, hold_period, iv=None, earnings_days=None):
    """A 1-10 risk score -- deliberately separate from the confidence score.
    Confidence answers "how much evidence supports this direction"; this
    answers "how much could being wrong (or even being right, but late) cost
    you." A high-confidence callout can still be high-risk (e.g. a volatile
    name with an imminent earnings print), and that distinction matters more
    than either number alone."""
    reasons = []

    if atr_pct >= config.RISK_ATR_HIGH_PCT:
        score = 3
        reasons.append(f"high volatility -- recent ATR is {atr_pct*100:.1f}% of price")
    elif atr_pct >= config.RISK_ATR_MED_PCT:
        score = 2
        reasons.append(f"moderate volatility -- recent ATR is {atr_pct*100:.1f}% of price")
    else:
        score = 1
        reasons.append(f"low volatility -- recent ATR is {atr_pct*100:.1f}% of price")

    if instrument == "option":
        dte = hold_period
        if dte <= config.RISK_DTE_VERY_SHORT:
            score += 4
            reasons.append(f"very short-dated option ({dte:.0f}d) -- high theta/gamma risk, can lose value fast even if the direction is right")
        elif dte <= config.RISK_DTE_SHORT:
            score += 3
            reasons.append(f"short-dated option ({dte:.0f}d) -- meaningful theta decay works against you")
        else:
            score += 2
            reasons.append(f"{dte:.0f}-day option -- more time for the thesis to play out, but still leveraged and decaying")

        if iv is not None:
            if iv >= config.RISK_IV_HIGH:
                score += 2
                reasons.append(f"high assumed implied volatility ({iv*100:.0f}%) -- expensive premium, exposed to IV crush")
            elif iv >= config.RISK_IV_MED:
                score += 1
    else:
        score += 1
        reasons.append("shares -- no expiration or time decay, but full dollar-for-dollar exposure to the move")

    if earnings_days is not None and earnings_days <= hold_period:
        score += 2
        reasons.append(f"earnings in {earnings_days}d, inside the expected hold window -- gap risk on the print")

    return max(1, min(10, score)), reasons


def _risk_label(score):
    if score <= 3:
        return "LOW"
    if score <= 6:
        return "MODERATE"
    return "HIGH"


def _build_summary(direction, tech_reasons, news_score, sector_bonus, earnings_penalty, confidence):
    """A one-sentence, plain-English synthesis of WHY this callout fired,
    built from the same structured facts as the itemized reasons list below
    it (not a separate guess) -- meant to be read in two seconds, with the
    bullet list underneath for anyone who wants the specifics verified."""
    dir_word = "bullish" if direction == 1 else "bearish"
    n_agree = sum(1 for r in tech_reasons if r.startswith(("MA/RSI", "Bollinger")))

    bits = [f"{n_agree} technical signal{'s' if n_agree != 1 else ''} agreeing {dir_word}"]
    if any(r.startswith("trend filter") and "aligned" in r for r in tech_reasons):
        bits.append("the trend filter confirms")
    if any(r.startswith("higher-timeframe") and "aligned" in r for r in tech_reasons):
        bits.append("the daily trend agrees")
    if any("relative strength" in r and ("leader" in r or "genuine weakness" in r) for r in tech_reasons):
        bits.append("relative strength backs it up")
    if news_score > 0:
        bits.append("news sentiment supports it")
    elif news_score < 0:
        bits.append("news sentiment actually conflicts (dragging the score down)")
    if sector_bonus > 0:
        bits.append("the whole sector is moving too")
    if earnings_penalty < 0:
        bits.append("but there's earnings risk in the window")

    if len(bits) == 1:
        joined = bits[0]
    else:
        joined = ", ".join(bits[:-1]) + ", and " + bits[-1]

    sentence = joined[0].upper() + joined[1:]
    return f"{sentence} -- confidence {confidence:.0f}/100."


def _sector_confirmation(ticker, direction):
    etf = get_sector_etf(ticker)
    if not etf:
        return 0, []
    try:
        sector_df = fetch_daily(etf, period="3mo")
    except Exception:
        return 0, []
    if len(sector_df) < 6:
        return 0, []
    ret5 = sector_df["close"].iloc[-1] / sector_df["close"].iloc[-6] - 1
    aligned = (direction == 1 and ret5 > 0.01) or (direction == -1 and ret5 < -0.01)
    if aligned:
        return config.SECTOR_CONFIRMATION_BONUS, [f"sector ({etf}) moved {ret5*100:+.1f}% over the last 5 sessions "
                                                    f"-- this move isn't isolated to one name"]
    return 0, []


def _hold_days_for_projection(instrument):
    """Converts each timeframe's time-stop into a day count, for re-pricing
    the option at a future date under the Black-Scholes assumption."""
    if instrument == "shares":
        return config.SWING_MAX_HOLD_DAYS
    return (config.INTRADAY_MAX_HOLD_BARS * 15) / (6.5 * 60)  # bars -> minutes -> fraction of a 6.5h trading day


def _compute_atr_val(df, spot):
    """ATR, with a floor fallback if there isn't enough history yet. Computed
    once per callout and shared between the exit plan and the risk score, so
    both are reading the same number rather than two separate calculations
    that could theoretically drift."""
    atr_series = atr_indicator(df, config.ATR_PERIOD)
    atr_val = atr_series.iloc[-1] if len(atr_series) else np.nan
    if np.isnan(atr_val) or atr_val <= 0:
        atr_val = spot * 0.02  # fallback: ~2% of price if ATR isn't computable yet (short history)
    return atr_val


def _build_exit_plan(direction, instrument, spot, atr_val, entry_option_price=None, option_type=None,
                      dte=None, strike=None, iv=None):
    """Produces concrete, numeric exit rules -- not just a score. Shares use
    ATR-scaled stop/target so the levels reflect this ticker's actual recent
    volatility; options use the config's percent-of-premium rule (theta decay
    makes a wide underlying-based stop meaningless on a short-dated contract).
    Every plan also has a time-stop and a condition-based invalidation rule,
    since "the setup stopped being true" can happen before either price level hits.

    For the option leg specifically, ALSO computes what the contract would
    actually be worth if the underlying's own price thesis (the ATR
    target/stop) plays out -- re-priced with Black-Scholes at a future date,
    same IV assumption. This directly answers "what is this betting the
    stock will do" instead of leaving the option's stop/target as an
    unrelated flat-percent rule."""
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

        if strike is not None and iv is not None:
            hold_days = _hold_days_for_projection(instrument)
            dte_at_projection = max(dte - hold_days, 0.5)
            proj_at_target = bs_price(shares_target, strike, dte_at_projection, iv, config.RISK_FREE_RATE, option_type)
            proj_at_stop = bs_price(shares_stop, strike, dte_at_projection, iv, config.RISK_FREE_RATE, option_type)
            plan["option"]["price_thesis"] = {
                "underlying_target": round(shares_target, 2),
                "est_option_value_at_target": round(proj_at_target, 2),
                "underlying_stop": round(shares_stop, 2),
                "est_option_value_at_stop": round(proj_at_stop, 2),
                "horizon_days": round(hold_days, 1),
                "basis": f"Black-Scholes re-priced at the underlying's own ATR target/stop, "
                         f"~{hold_days:.0f} days out, same {iv*100:.0f}% IV assumption -- this is what the "
                         f"option is actually betting on, not just a flat premium percentage",
            }

    return plan


def scan_ticker(ticker, timeframe_name, daily_df=None, benchmark_df=None):
    tf_cfg = TIMEFRAME_STRATEGIES[timeframe_name]
    use_cached_daily = tf_cfg["interval"] == "1d" and daily_df is not None
    try:
        df = daily_df if use_cached_daily else _fetch(ticker, tf_cfg)
    except Exception:
        return None

    if df is None or len(df) < 30:
        return None

    rs_value = relative_strength_excess(df if tf_cfg["interval"] == "1d" else daily_df, benchmark_df) \
        if (benchmark_df is not None and (tf_cfg["interval"] == "1d" or daily_df is not None)) else None

    direction, tech_score, tech_reasons, discarded = backtestable_score(
        df, tf_cfg["strategies"],
        trend_filter_period=tf_cfg["trend_filter_period"],
        trend_filter_hard=tf_cfg["trend_filter_hard"],
        daily_df=(daily_df if tf_cfg["interval"] != "1d" else None),
        rs_value=rs_value,
    )
    if direction == 0 or discarded:
        return None

    news_score, news_reasons = _news_score(ticker, direction)
    reasons = tech_reasons + news_reasons

    # Cheap pre-filter before spending extra network calls (earnings/sector) on a
    # candidate that can't reach the confidence bar even with every remaining bonus.
    if tech_score + news_score + config.SECTOR_CONFIRMATION_BONUS < config.MIN_CONFIDENCE_SCORE:
        return None

    spot = float(df["close"].iloc[-1])
    option_type = "call" if direction == 1 else "put"
    strike = select_strike(spot, config.STRIKE_OFFSET_PCT, option_type)
    iv = config.DEFAULT_IV.get(ticker, config.DEFAULT_IV_FALLBACK)
    dte = tf_cfg["target_dte"]
    est_option_price = bs_price(spot, strike, dte, iv, config.RISK_FREE_RATE, option_type)

    earnings_days = days_until_earnings(ticker)
    earnings_penalty, earnings_reasons = _earnings_penalty(earnings_days, dte)
    sector_bonus, sector_reasons = _sector_confirmation(ticker, direction)
    reasons = reasons + earnings_reasons + sector_reasons

    confidence = max(0.0, min(100.0, tech_score + news_score + earnings_penalty + sector_bonus))
    if confidence < config.MIN_CONFIDENCE_SCORE:
        return None

    suggested_action = "BUY SHARES" if tf_cfg["instrument"] == "shares" and direction == 1 else (
        "SHORT / SELL SHARES" if tf_cfg["instrument"] == "shares" else f"BUY {option_type.upper()}"
    )

    atr_val = _compute_atr_val(df, spot)
    exit_plan = _build_exit_plan(
        direction, tf_cfg["instrument"], spot, atr_val,
        entry_option_price=est_option_price, option_type=option_type, dte=dte, strike=strike, iv=iv,
    )
    expiration_date = (datetime.now() + timedelta(days=dte)).strftime("%Y-%m-%d")

    hold_period = _hold_days_for_projection(tf_cfg["instrument"])
    atr_pct = atr_val / spot if spot else 0
    shares_risk_score, shares_risk_reasons = _risk_level("shares", atr_pct, hold_period, earnings_days=earnings_days)
    option_risk_score, option_risk_reasons = _risk_level("option", atr_pct, dte, iv=iv, earnings_days=earnings_days)
    risk = {
        "shares": {"score": shares_risk_score, "level": _risk_label(shares_risk_score), "reasons": shares_risk_reasons},
        "option": {"score": option_risk_score, "level": _risk_label(option_risk_score), "reasons": option_risk_reasons},
    }
    primary_risk = risk["shares"] if tf_cfg["instrument"] == "shares" else risk["option"]

    summary = _build_summary(direction, tech_reasons, news_score, sector_bonus, earnings_penalty, confidence)

    return {
        "ticker": ticker,
        "timeframe": timeframe_name,
        "direction": "BULLISH" if direction == 1 else "BEARISH",
        "suggested_action": suggested_action,
        "instrument_primary": tf_cfg["instrument"],
        "summary": summary,
        "option_alt": {
            "type": option_type, "strike": strike, "dte_days": dte,
            "expiration_date": expiration_date,
            "est_price": round(est_option_price, 2), "iv_assumed": iv,
        },
        "confidence_score": round(confidence, 1),
        "risk_score": primary_risk["score"],
        "risk_level": primary_risk["level"],
        "risk": risk,
        "spot": round(spot, 2),
        "reasons": reasons,
        "exit_plan": exit_plan,
        "as_of": datetime.now().isoformat(timespec="seconds"),
    }


def scan_universe(tickers, timeframe_names=None):
    timeframe_names = timeframe_names or list(TIMEFRAME_STRATEGIES.keys())
    unique_tickers = list(dict.fromkeys(tickers))

    try:
        benchmark_df = fetch_daily(config.RS_BENCHMARK, period=TIMEFRAME_STRATEGIES["swing"]["period"])
    except Exception as e:
        print(f"[scanner] couldn't fetch relative-strength benchmark {config.RS_BENCHMARK}: {e}")
        benchmark_df = None

    daily_cache = {}
    for ticker in unique_tickers:
        try:
            daily_cache[ticker] = fetch_daily(ticker, period=TIMEFRAME_STRATEGIES["swing"]["period"])
        except Exception as e:
            print(f"[scanner] {ticker} daily fetch failed: {e}")

    callouts = []
    near_misses = []
    swing_cfg = TIMEFRAME_STRATEGIES["swing"]
    for ticker in unique_tickers:
        daily_df = daily_cache.get(ticker)
        if daily_df is None or len(daily_df) < 30:
            continue
        had_swing_callout = False
        for tf_name in timeframe_names:
            try:
                result = scan_ticker(ticker, tf_name, daily_df=daily_df, benchmark_df=benchmark_df)
            except Exception as e:
                print(f"[scanner] {ticker}/{tf_name} failed: {e}")
                result = None
            if result:
                callouts.append(result)
                if tf_name == "swing":
                    had_swing_callout = True

        if had_swing_callout or "swing" not in timeframe_names:
            continue

        # Near-miss diagnostic (swing only -- reuses the daily data already
        # fetched above, no extra network calls): both swing strategies only
        # fire a signal on the exact bar an MA crossover / BB squeeze breakout
        # happens, so most tickers on most days have no signal at all -- a
        # scan finding zero callouts is very often correct, not broken. Check
        # the last few bars (not just today) for the most recent real event on
        # each ticker, whether or not it would have cleared the confidence
        # bar, so a scan that finds nothing actionable still shows *something*
        # rather than looking dead. Skipped for a ticker that already produced
        # a real swing callout above, so nothing shows up twice.
        for bars_ago in range(RECENT_SIGNAL_LOOKBACK_BARS):
            idx = -1 - bars_ago
            if -idx > len(daily_df):
                break
            try:
                rs_value = relative_strength_excess(daily_df, benchmark_df, idx=idx) if benchmark_df is not None else None
                direction, tech_score, reasons, discarded = backtestable_score(
                    daily_df, swing_cfg["strategies"],
                    trend_filter_period=swing_cfg["trend_filter_period"],
                    trend_filter_hard=swing_cfg["trend_filter_hard"],
                    idx=idx, rs_value=rs_value,
                )
            except Exception:
                direction, tech_score, reasons, discarded = 0, 0, [], False
            if direction != 0 and not discarded:
                near_misses.append({
                    "ticker": ticker, "timeframe": "swing",
                    "direction": "BULLISH" if direction == 1 else "BEARISH",
                    "tech_score": tech_score, "tech_score_max": config.BACKTESTABLE_SCORE_MAX,
                    "bars_ago": bars_ago,
                    "reasons": reasons,
                })
                break  # most recent event only, not every bar further back too

    callouts.sort(key=lambda c: c["confidence_score"], reverse=True)
    near_misses.sort(key=lambda m: m["tech_score"], reverse=True)
    return callouts, near_misses[:5]
