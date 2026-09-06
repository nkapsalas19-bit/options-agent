"""
Ticker universe for the market-wide scanner.

Tries to pull the live S&P 500 constituent list from Wikipedia (needs network
access + pandas' lxml/html5lib dependency). If that fails -- no network,
table format changed, package missing -- falls back to a curated subset of
roughly 100 large, liquid, optionable S&P 500 names. That fallback is NOT the
full index; it exists so the scanner still runs offline or when Wikipedia is
unreachable. For full control, set config.SCANNER_UNIVERSE_MODE to
"watchlist" and list exactly the tickers you want in config.SCANNER_WATCHLIST.
"""
import config

FALLBACK_LIQUID_SUBSET = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA", "BRK-B", "JPM",
    "V", "UNH", "XOM", "JNJ", "WMT", "MA", "PG", "HD", "CVX", "MRK",
    "ABBV", "PEP", "KO", "COST", "AVGO", "LLY", "BAC", "PFE", "TMO", "CSCO",
    "ACN", "MCD", "ABT", "DHR", "LIN", "ADBE", "CRM", "NFLX", "AMD", "INTC",
    "TXN", "NKE", "PM", "UPS", "NEE", "RTX", "HON", "QCOM", "UNP", "LOW",
    "IBM", "GE", "CAT", "BA", "GS", "SBUX", "INTU", "AMAT", "DE", "BLK",
    "MDT", "ISRG", "NOW", "AXP", "PLD", "SPGI", "GILD", "BKNG", "ADI", "MMC",
    "SYK", "VRTX", "TJX", "MO", "CB", "LMT", "ETN", "ZTS", "C", "SO",
    "DUK", "BDX", "CI", "PGR", "MU", "FI", "SCHW", "REGN", "PANW", "SNPS",
    "CDNS", "KLAC", "ORCL", "SHW", "WFC", "COP", "EOG", "PYPL", "F", "GM",
    "SPY", "QQQ",
]


def _fetch_live_sp500():
    import pandas as pd
    tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
    df = tables[0]
    tickers = df["Symbol"].astype(str).str.replace(".", "-", regex=False).tolist()
    if len(tickers) < 400:
        raise ValueError(f"unexpected table shape ({len(tickers)} rows), refusing to trust it")
    return tickers


def get_scan_universe():
    if config.SCANNER_UNIVERSE_MODE == "watchlist":
        return list(dict.fromkeys(config.SCANNER_WATCHLIST))

    try:
        return _fetch_live_sp500()
    except Exception as e:
        print(f"[universe] live S&P 500 fetch failed ({e}); using {len(FALLBACK_LIQUID_SUBSET)}-ticker fallback subset instead")
        return FALLBACK_LIQUID_SUBSET
