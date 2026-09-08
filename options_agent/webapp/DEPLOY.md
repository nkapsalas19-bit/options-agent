# Deploying to Render

Render can't be automated from here — you'll need to click through their
dashboard yourself with your own account. This is the exact path, in order.

Your code is already pushed to GitHub (`nkapsalas19-bit/options-agent`), so
you can skip straight to creating the Render service.

## Repo layout — read this before filling in "Root Directory"

This repository's root is **not** the same folder as the Python package.
The layout is:

```
options-agent/              <- repo root (what Render clones)
  options_agent/            <- the actual Python package, one level in
    webapp/
      app.py
      requirements.txt
    scanner.py
    config.py
    ...
```

That extra `options_agent/` layer is easy to miss because the repo is named
`options-agent` (hyphen) and the folder inside it is `options_agent`
(underscore) — nearly identical names for two different things. Whatever
you set **Root Directory** to on Render, it's relative to the repo root
above, so it must be **`options_agent/webapp`**, not just `webapp`. Getting
this wrong is exactly what produces a build failure like:

```
ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirements.txt'
```

(Render looked for `requirements.txt` in the wrong folder because Root
Directory didn't point at the folder that actually contains it.)

## Create the Render Web Service

1. Go to [render.com](https://render.com) and log in.
2. Click **New +** → **Web Service**.
3. Select the `nkapsalas19-bit/options-agent` repo.
4. Find the **Branch** field/dropdown and select
   `claude/ai-stock-trading-bot-2gj4lp` — that's the branch with all the
   scanner/dashboard work on it, not `main`.
5. Fill in the configuration:

| Field | Value |
|---|---|
| **Root Directory** | `options_agent/webapp` |
| **Runtime** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `gunicorn app:app --bind 0.0.0.0:$PORT --worker-class gthread --threads 4 --timeout 300` |
| **Instance Type** | Free |

The "Scan Now"/"Run Backtest" buttons run in a background thread inside the
same process, while the page keeps polling a separate status endpoint for
progress. Plain gunicorn (`--worker-class sync`, the default) handles one
request at a time per worker and can starve that polling under load; `gthread`
with a few threads is built for exactly this pattern. `--timeout 300` gives
a slow scan (large universe, or a sluggish free-tier instance) more room
before gunicorn decides the worker is unresponsive and restarts it — a
restart wipes the in-progress job's state, which looks like "it's been
running forever with nothing happening."

6. Under **Environment Variables**, add:

| Key | Value |
|---|---|
| `DASHBOARD_PASSWORD` | pick your own password |
| `FLASK_SECRET_KEY` | any long random string (mashing your keyboard for 30-40 characters works fine) |
| `NTFY_TOPIC` | optional — a long, hard-to-guess name for phone push notifications; see README's "Push notifications" section |

7. Click **Create Web Service** (or, if you already created the service with
   the wrong Root Directory, open it → **Settings** → fix **Root Directory**
   → save → **Manual Deploy** → **Deploy latest commit**, since a Root
   Directory change doesn't always trigger a rebuild by itself).
8. First build takes 2-3 minutes — watch it in the **Logs** tab. It should
   now get past the `pip install` step instead of failing immediately.
9. Once it's live, Render gives you a URL like
   `https://your-app-name.onrender.com`. That's your public dashboard,
   password-gated with whatever you set in step 6.

## Things worth knowing about the free tier

- **Cold starts**: Render's free tier spins the service down after 15
  minutes of no traffic. The next visit takes 30-60 seconds to wake back up.
  Fine for personal use, just don't be surprised by the delay.
- **Every `git push` to this branch auto-redeploys.** Render watches the
  branch you selected in step 4.
- If the build fails, check the **Logs** tab first — the two most common
  causes are Root Directory pointing at the wrong folder (see above) and a
  missing package in `options_agent/webapp/requirements.txt`.
- **If "Scan Now" spins for a long time with nothing happening**: check the
  **Logs** tab for lines starting with `[scanner]`. The scanner's default
  universe (`config.SCANNER_UNIVERSE_MODE = "watchlist"`, 10 tickers) should
  finish in well under a minute even on the free tier. If it's still slow or
  the logs show repeated fetch failures, Yahoo Finance is likely
  rate-limiting or blocking requests from Render's IP range — a known
  limitation of the free `yfinance` data source on cloud hosting in general,
  not specific to this app. There's no code fix for that short of a paid
  market-data API; retrying later or reducing `SCANNER_WATCHLIST` further
  are the practical workarounds.

## Why "Root Directory: options_agent/webapp" matters

`app.py` adds its parent directory to `sys.path` at runtime so it can import
the sibling modules (`data_fetcher.py`, `strategies.py`, `scanner.py`, etc.)
that live in `options_agent/`, one level up from `webapp/`. That works
regardless of Render's working directory, so you don't need to change any
code — just point Root Directory at the actual `webapp` folder inside
`options_agent/` and it'll find everything from there.
