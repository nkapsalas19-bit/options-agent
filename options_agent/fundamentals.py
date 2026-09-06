"""
Live-only enrichment data: upcoming earnings dates and sector classification.
Both are best-effort -- yfinance's earnings-calendar and company-profile
endpoints are less reliable than its price data (missing for some tickers,
occasionally stale), so every function here degrades to "no information"
(None) on any failure rather than raising. Called only for candidates that
already passed the technical+news score gate, since each of these is an
extra network round-trip per ticker.
"""
import pandas as pd
import yfinance as yf

SECTOR_ETF = {
    "Technology": "XLK",
    "Financial Services": "XLF",
    "Healthcare": "XLV",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Basic Materials": "XLB",
    "Communication Services": "XLC",
}


def days_until_earnings(ticker):
    """Returns an int day count to the next known earnings date, or None if
    unavailable/unknown. Never raises."""
    try:
        df = yf.Ticker(ticker).get_earnings_dates(limit=6)
        if df is None or df.empty:
            return None
        now = pd.Timestamp.now(tz=df.index.tz) if df.index.tz is not None else pd.Timestamp.now()
        upcoming = df[df.index >= now]
        if upcoming.empty:
            return None
        return int((upcoming.index.min() - now).days)
    except Exception:
        return None


def get_sector_etf(ticker):
    """Maps a ticker to its GICS sector's SPDR sector ETF, or None if the
    sector is unknown/unmapped. Never raises."""
    try:
        info = yf.Ticker(ticker).info
        sector = info.get("sector")
        return SECTOR_ETF.get(sector)
    except Exception:
        return None
