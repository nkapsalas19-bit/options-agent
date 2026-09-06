"""Convenience entry point that runs the web app with the background
ingestion scheduler enabled:

    python -m realestate_tracker.run

For most local development, `python -m realestate_tracker.cli serve` (no
scheduler, manual "Check for new listings" button only) is simpler and
avoids duplicate scheduler instances under the Flask reloader.
"""
from .app import create_app
from .scheduler import start_scheduler

app = create_app()
start_scheduler(app)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, use_reloader=False)
