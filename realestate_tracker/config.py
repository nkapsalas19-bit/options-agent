"""Central configuration and tunable financial assumptions.

All the "how do we turn a listing into a cap rate" assumptions live here so
they're easy to see and adjust in one place instead of buried in scoring
logic.
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Config:
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'realestate.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    # --- Operating expense assumptions (used when actuals aren't known) ---
    VACANCY_RATE = float(os.environ.get("VACANCY_RATE", 0.05))
    INSURANCE_PCT_OF_RENT = float(os.environ.get("INSURANCE_PCT_OF_RENT", 0.04))
    MAINTENANCE_PCT_OF_RENT = float(os.environ.get("MAINTENANCE_PCT_OF_RENT", 0.08))
    MANAGEMENT_PCT_OF_RENT = float(os.environ.get("MANAGEMENT_PCT_OF_RENT", 0.08))

    # --- Financing assumptions for cash-on-cash / debt service estimates ---
    DOWN_PAYMENT_PCT = float(os.environ.get("DOWN_PAYMENT_PCT", 0.25))
    INTEREST_RATE = float(os.environ.get("INTEREST_RATE", 0.07))
    LOAN_TERM_YEARS = int(os.environ.get("LOAN_TERM_YEARS", 30))
    CLOSING_COST_PCT = float(os.environ.get("CLOSING_COST_PCT", 0.03))

    # --- Thresholds used for scoring / "is this a good deal" ---
    TARGET_CAP_RATE = float(os.environ.get("TARGET_CAP_RATE", 0.06))
    TARGET_CASH_ON_CASH = float(os.environ.get("TARGET_CASH_ON_CASH", 0.08))

    # Optional real data source credentials (leave unset to skip that source)
    RENTCAST_API_KEY = os.environ.get("RENTCAST_API_KEY")

    # How many synthetic "new to market" listings the sample source produces
    # per ingestion run (only relevant when no real source is configured).
    SAMPLE_NEW_LISTINGS_PER_RUN = int(os.environ.get("SAMPLE_NEW_LISTINGS_PER_RUN", 3))

    # Ingestion cadence in minutes when running under the scheduler.
    INGEST_INTERVAL_MINUTES = int(os.environ.get("INGEST_INTERVAL_MINUTES", 60))
