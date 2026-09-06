"""
Email alerts via Gmail SMTP. Free, no third-party API key -- just a Gmail
account and an App Password (see config.py for the env vars this needs and
how to generate one).

If GMAIL_ADDRESS / GMAIL_APP_PASSWORD aren't set, send_email_alert() prints
the alert to the console instead of raising, so the scanner keeps running
even before you've wired up credentials.
"""
import os
import smtplib
import ssl
from email.mime.text import MIMEText


def send_email_alert(subject, body):
    sender = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    recipient = os.environ.get("ALERT_TO_EMAIL", sender)

    if not sender or not app_password:
        print("[alerts] GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set -- printing alert instead of emailing it:")
        print(f"  Subject: {subject}")
        print(f"  {body}")
        return False

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(sender, app_password)
            server.sendmail(sender, [recipient], msg.as_string())
        return True
    except Exception as e:
        print(f"[alerts] failed to send email ({e}); alert was:")
        print(f"  Subject: {subject}")
        print(f"  {body}")
        return False
