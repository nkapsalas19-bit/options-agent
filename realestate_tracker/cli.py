"""Command-line entry points:

    python -m realestate_tracker.cli init-db
    python -m realestate_tracker.cli seed --criteria --listings 15
    python -m realestate_tracker.cli ingest
    python -m realestate_tracker.cli serve
"""
import argparse

from .app import create_app
from .config import Config
from .extensions import db
from .ingestion.pipeline import run_ingestion
from .ingestion.sample_source import SampleDataSource
from .models import Criteria


def cmd_init_db(args):
    app = create_app()
    with app.app_context():
        db.create_all()
    print("Database initialized.")


def cmd_seed(args):
    app = create_app()
    with app.app_context():
        if args.criteria:
            _seed_default_criteria()
        summary = run_ingestion(app.re_config, sources=[SampleDataSource(count=args.listings)])
        print(f"Seeded {summary['new']} new properties, {summary['recommendations']} recommendations.")


def _seed_default_criteria():
    if Criteria.query.count() > 0:
        return
    profiles = [
        dict(
            name="Sunbelt buy & hold, 6%+ cap rate",
            location_list=["Austin", "Tampa", "Phoenix", "Raleigh"],
            property_type_list=["residential", "multifamily"],
            min_cap_rate=0.06,
            strategy="buy_and_hold",
        ),
        dict(
            name="Midwest multifamily / mixed-use development",
            location_list=["Columbus", "Kansas City", "Indianapolis"],
            property_type_list=["multifamily", "mixed_use", "land"],
            min_lot_size_sqft=5000,
            strategy="development",
        ),
        dict(
            name="Any market, any strategy, $1M+ commercial",
            location_list=[],
            property_type_list=["commercial", "industrial"],
            min_price=1_000_000,
            strategy="both",
        ),
    ]
    for p in profiles:
        c = Criteria(
            name=p["name"],
            min_price=p.get("min_price"),
            max_price=p.get("max_price"),
            min_cap_rate=p.get("min_cap_rate"),
            min_lot_size_sqft=p.get("min_lot_size_sqft"),
            strategy=p.get("strategy", "both"),
            active=True,
        )
        c.location_list = p.get("location_list", [])
        c.property_type_list = p.get("property_type_list", [])
        c.zoning_code_list = p.get("zoning_code_list", [])
        db.session.add(c)
    db.session.commit()
    print("Seeded default saved searches.")


def cmd_ingest(args):
    app = create_app()
    with app.app_context():
        summary = run_ingestion(app.re_config)
        print(summary)


def cmd_serve(args):
    app = create_app()
    app.run(host=args.host, port=args.port, debug=args.debug)


def main():
    parser = argparse.ArgumentParser(description="Real Estate Deal Tracker CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db").set_defaults(func=cmd_init_db)

    seed_parser = sub.add_parser("seed")
    seed_parser.add_argument("--listings", type=int, default=15)
    seed_parser.add_argument("--criteria", action="store_true", default=True)
    seed_parser.set_defaults(func=cmd_seed)

    sub.add_parser("ingest").set_defaults(func=cmd_ingest)

    serve_parser = sub.add_parser("serve")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=5050)
    serve_parser.add_argument("--debug", action="store_true")
    serve_parser.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
