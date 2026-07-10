"""
Pulls OHLCV data from yfinance for SPY/QQQ.
Two modes: intraday (for live signal generation) and daily (for long backtests,
since yfinance caps intraday history at ~60 days).
"""
import yfinance as yf
import pandas as pd


def fetch_intraday(ticker, interval="5m", period="60d"):
    df = yf.download(ticker, interval=interval, period=period, progress=False, auto_adjust=True)
    return _clean(df, ticker)


def fetch_daily(ticker, period="3y"):
    df = yf.download(ticker, interval="1d", period=period, progress=False, auto_adjust=True)
    return _clean(df, ticker)


def _clean(df, ticker):
    if df.empty:
        raise ValueError(f"No data returned for {ticker}. Check ticker/interval/period "
                          f"or network access to Yahoo Finance.")
    # yfinance sometimes returns MultiIndex columns even for a single ticker
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)
    df.index.name = "datetime"
    return df[["open", "high", "low", "close", "volume"]].dropna()


def latest_price(ticker):
    df = fetch_intraday(ticker, interval="1m", period="1d")
    return float(df["close"].iloc[-1])


def get_expirations(ticker):
    """Real available option expiration dates for this ticker, right now."""
    t = yf.Ticker(ticker)
    return list(t.options)


def get_option_chain(ticker, expiration):
    """Real live options chain (bid/ask/last/volume/OI/IV) for one expiration.
    This is a live snapshot -- yfinance does not provide historical chains,
    but for 'right now' this is real market data, not an estimate."""
    t = yf.Ticker(ticker)
    chain = t.option_chain(expiration)
    calls = chain.calls[["strike", "bid", "ask", "lastPrice", "volume", "openInterest", "impliedVolatility"]]
    puts = chain.puts[["strike", "bid", "ask", "lastPrice", "volume", "openInterest", "impliedVolatility"]]
    return calls, puts
