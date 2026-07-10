# Live Dashboard

A web dashboard on top of the existing agent: candlestick chart with the
active strategy's overlay lines, buy/sell markers on the chart itself, and a
live alert panel (suggestion, strike, expiration, entry/target/stop) that
refreshes every 60 seconds.

## Run it locally

```bash
cd options_agent
pip install flask
python webapp/app.py
```

Then open `http://localhost:5000` **in your browser** — the URL is printed
in the terminal when the server starts. Switch between SPY/QQQ and the three
strategies with the controls in the top bar.

### If you see an error or a blank page with no charts

This almost always means the HTML file was opened directly (double-clicked,
or opened via a `file:///...` URL) instead of through the Flask server. The
page has no data of its own — it fetches everything from the backend at
`/api/chart/...` and `/api/alert/...`, and those routes only exist while
`python webapp/app.py` is running. Always start the server first, then visit
the `localhost` URL it prints.

If you did start the server and still see an error, an error banner will
now appear directly on the chart telling you what failed (connection issue,
wrong password, etc.) instead of failing silently.

## Password protection

The dashboard is gated behind a login page. Before hosting this anywhere
public, set your own password and secret key as environment variables:

```bash
export DASHBOARD_PASSWORD="your-own-password-here"
export FLASK_SECRET_KEY="some-long-random-string"
python webapp/app.py
```

Without these set, it falls back to a default password (`changeme123`) so it
runs out of the box locally — **do not leave this default if you deploy it
somewhere reachable from the internet.** The session cookie lasts 7 days, so
you won't need to log in every time you reload the page.

This is basic protection (one shared password, one cookie) — fine for
keeping a casual visitor out, but not bank-grade security. Don't put
anything more sensitive than this dashboard's read-only signals behind it.

## How it works

- `webapp/app.py` — Flask backend. Pulls daily OHLCV via yfinance, runs the
  selected strategy from `strategies.py`, and exposes two endpoints:
  - `/api/chart/<ticker>?strategy=...` — candles + overlay lines (moving
    averages or Bollinger bands, depending on strategy) + buy/sell markers
  - `/api/alert/<ticker>?strategy=...` — current suggestion (CALL/PUT/NONE),
    strike, expiration date, and Black-Scholes-estimated entry/target/stop
- `webapp/templates/index.html` — single-page frontend using
  [lightweight-charts](https://github.com/tradingview/lightweight-charts)
  (the open-source charting library, not the paid TradingView platform) for
  rendering, vanilla JS polling the two endpoints every 60s.
- Results are cached server-side for 60 seconds so rapid page reloads don't
  hammer Yahoo Finance.

## Important: this is a local app, not yet a hosted website

Right now this runs on your machine at `localhost:5000`. To make it an
actual live website reachable from anywhere, you'd deploy the Flask app to a
host — Render, Railway, Fly.io, or a small VPS all work well for a Flask app
this size. None of that is done for you here since it requires your own
hosting account and a decision on where to put it; happy to help with
whichever one you pick.

## The "SYNTHETIC DATA" flag

If yfinance can't be reached (rate-limited, network issue, or — like when I
built this — running somewhere without internet access to Yahoo), the
backend falls back to synthetic data so the dashboard still renders instead
of showing a blank page. You'll see an amber "SYNTHETIC DATA" badge in the
top-right of the chart when this happens. On your own machine with normal
internet access, this should not appear — if it does, something's wrong with
the data fetch and you should check `webapp/app.py`'s console output.

## What the panel numbers mean (and don't)

Same caveat as the core backtest: entry/target/stop are Black-Scholes
estimates from historical/live underlying price, not real bid-ask quotes from
an options exchange. Before trusting a "CALL" or "PUT" suggestion with real
money, cross-check the actual option's live quote in your broker — the
theoretical price here can diverge from what you'd actually pay, especially
around volatility spikes.
