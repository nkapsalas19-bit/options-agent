"""Background scheduler that periodically checks for new listings, so the
platform proactively surfaces recommendations instead of requiring a manual
click every time.

Usage:
    from realestate_tracker.app import create_app
    from realestate_tracker.scheduler import start_scheduler

    app = create_app()
    start_scheduler(app)
    app.run()
"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler

from .ingestion.pipeline import run_ingestion

logger = logging.getLogger(__name__)


def start_scheduler(app):
    scheduler = BackgroundScheduler()

    def job():
        with app.app_context():
            summary = run_ingestion(app.re_config)
            logger.info("Ingestion cycle complete: %s", summary)

    scheduler.add_job(
        job,
        "interval",
        minutes=app.config["INGEST_INTERVAL_MINUTES"],
        id="ingest_listings",
    )
    scheduler.start()
    # Run once immediately so the dashboard isn't empty on first boot.
    job()
    return scheduler
