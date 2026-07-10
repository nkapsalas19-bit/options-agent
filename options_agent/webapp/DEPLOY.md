# Deploying to Render

Render can't be automated from here — you'll need to click through their
dashboard yourself with your own account. This is the exact path, in order.

## 1. Push the code to GitHub

From inside the `options_agent` folder:

```bash
git init
git add .
git commit -m "Initial commit"
```

Create a new repo on GitHub (github.com → New repository), then:

```bash
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
git branch -M main
git push -u origin main
```

## 2. Create the Render Web Service

1. Go to [render.com](https://render.com) and log in (or sign up — no credit
   card needed for the free tier).
2. Click **New +** → **Web Service**.
3. Connect your GitHub account if you haven't already, then select the repo
   you just pushed.
4. Fill in the configuration:

| Field | Value |
|---|---|
| **Root Directory** | `webapp` (since `app.py` lives inside `options_agent/webapp/`) |
| **Runtime** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `gunicorn app:app --bind 0.0.0.0:$PORT --timeout 120` |
| **Instance Type** | Free |

5. Under **Environment Variables**, add:

| Key | Value |
|---|---|
| `DASHBOARD_PASSWORD` | pick your own password |
| `FLASK_SECRET_KEY` | any long random string (e.g. generate one with `python -c "import secrets; print(secrets.token_hex(32))"`) |

6. Click **Create Web Service**. First build takes 2-3 minutes — you can
   watch it in the Logs tab.
7. Once it's live, Render gives you a URL like
   `https://your-app-name.onrender.com`. That's your public dashboard,
   password-gated.

## Things worth knowing about the free tier

- **Cold starts**: Render's free tier spins the service down after 15
  minutes of no traffic. The next visit takes 30-60 seconds to wake back up.
  Fine for personal use, just don't be surprised by the delay.
- **Every `git push` auto-redeploys.** Change something locally, commit,
  push — Render rebuilds automatically.
- If the build fails, check the **Logs** tab first — almost always a missing
  package in `webapp/requirements.txt` or a typo in the start command.

## Why "Root Directory: webapp" matters

`app.py` adds its parent directory to `sys.path` at runtime so it can import
the sibling modules (`data_fetcher.py`, `strategies.py`, etc.) that live in
`options_agent/`, not `options_agent/webapp/`. That works regardless of
Render's working directory, so you don't need to change any code — just
point Render's Root Directory at `webapp` and it'll find everything.
