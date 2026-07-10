"""
Black-Scholes option pricing.

WHY THIS EXISTS: yfinance (free data) only exposes the CURRENT options chain,
not historical chains. To backtest an options strategy over the past 1-3 years
we need *some* way to price a hypothetical call/put on a historical date.
Black-Scholes, fed with the real historical underlying price and a reasonable
IV estimate, is the standard workaround. It's an approximation, not ground
truth -- real fills would differ due to actual smile/skew, bid-ask spread,
and liquidity. Treat backtest P&L as directional signal quality, not a
promise of live returns.
"""
import numpy as np
from scipy.stats import norm


def bs_price(spot, strike, days_to_expiry, iv, rate, option_type="call"):
    """Black-Scholes European option price."""
    if days_to_expiry <= 0:
        # at expiry, price is intrinsic value
        if option_type == "call":
            return max(spot - strike, 0.0)
        else:
            return max(strike - spot, 0.0)

    t = days_to_expiry / 365.0
    if iv <= 0 or t <= 0:
        return max(spot - strike, 0.0) if option_type == "call" else max(strike - spot, 0.0)

    d1 = (np.log(spot / strike) + (rate + 0.5 * iv ** 2) * t) / (iv * np.sqrt(t))
    d2 = d1 - iv * np.sqrt(t)

    if option_type == "call":
        price = spot * norm.cdf(d1) - strike * np.exp(-rate * t) * norm.cdf(d2)
    else:
        price = strike * np.exp(-rate * t) * norm.cdf(-d2) - spot * norm.cdf(-d1)

    return max(price, 0.01)  # floor to avoid zero/negative from numerical edge cases


def bs_delta(spot, strike, days_to_expiry, iv, rate, option_type="call"):
    t = max(days_to_expiry / 365.0, 1e-6)
    d1 = (np.log(spot / strike) + (rate + 0.5 * iv ** 2) * t) / (iv * np.sqrt(t))
    if option_type == "call":
        return norm.cdf(d1)
    else:
        return norm.cdf(d1) - 1


def select_strike(spot, offset_pct, option_type="call"):
    """Pick a strike near-the-money, offset by a percentage of spot.
    Rounds to nearest $1 strike (SPY/QQQ trade in $1 increments)."""
    if option_type == "call":
        raw = spot * (1 + offset_pct)
    else:
        raw = spot * (1 - offset_pct)
    return round(raw)
