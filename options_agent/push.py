"""
Push notifications via ntfy.sh -- free, no account required. Set the
NTFY_TOPIC environment variable to a long, hard-to-guess name (it's the
only thing standing between "anyone who knows it" and your notifications --
ntfy topics are public by default unless self-hosted), install the ntfy
app (iOS/Android, or use a browser) and subscribe to that same topic name.
Every send_push() call becomes a phone notification within seconds.

If NTFY_TOPIC isn't set, send_push() prints instead of failing, matching
alerts.py's pattern -- the platform keeps working before you've wired this
up, it just doesn't buzz your phone yet.
"""
import os

import requests

NTFY_BASE_URL = os.environ.get("NTFY_BASE_URL", "https://ntfy.sh")


def send_push(title, message, priority="default", tags=None):
    """priority: 'min', 'low', 'default', 'high', or 'urgent' (ntfy's scale --
    'urgent' also bypasses the phone's silent/DND mode on most setups).
    tags: optional list of ntfy emoji-shortcode tags, e.g. ['chart_with_upwards_trend']."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("[push] NTFY_TOPIC not set -- printing notification instead of sending it:")
        print(f"  {title}: {message}")
        return False

    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = ",".join(tags)

    try:
        resp = requests.post(f"{NTFY_BASE_URL}/{topic}", data=message.encode("utf-8"),
                              headers=headers, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"[push] failed to send ({e}); notification was: {title}: {message}")
        return False
