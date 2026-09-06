"""Orchestrates one ingestion cycle:

    1. Pull raw listings from every configured DataSource.
    2. Upsert them into the Property table (keyed on source+external_id).
    3. Re-score every touched property (scoring.py).
    4. Match active Criteria profiles against new/updated properties and
       create/update Recommendation rows with a match score + reasons.

This is what both the manual "Check for new listings" button and the
background scheduler call.
"""
from datetime import datetime, timezone

from ..extensions import db
from ..models import Criteria, Property, Recommendation
from ..scoring import score_property
from .sample_source import SampleDataSource
from .rentcast_source import RentCastDataSource


def get_configured_sources(cfg):
    """Build the list of DataSources based on config/env. Always includes
    the sample source so the app is useful with zero setup; adds RentCast
    automatically once an API key is present."""
    sources = [SampleDataSource(count=cfg.SAMPLE_NEW_LISTINGS_PER_RUN)]
    if cfg.RENTCAST_API_KEY:
        sources.append(RentCastDataSource(api_key=cfg.RENTCAST_API_KEY))
    return sources


def upsert_listing(raw):
    """Insert or update a Property row from a raw listing dict. Returns the
    Property instance (added to the session but not yet committed)."""
    source = raw["source"]
    external_id = raw["external_id"]

    prop = Property.query.filter_by(source=source, external_id=external_id).first()
    is_new = prop is None
    if prop is None:
        prop = Property(source=source, external_id=external_id)
        db.session.add(prop)

    for field in (
        "address", "city", "state", "zip_code", "county", "latitude", "longitude",
        "property_type", "zoning_code", "zoning_description", "allowed_units",
        "existing_units", "lot_size_sqft", "building_size_sqft", "year_built",
        "list_price", "annual_property_tax", "estimated_rent_monthly", "hoa_monthly",
        "status", "listing_url", "listed_date",
    ):
        if field in raw and raw[field] is not None:
            setattr(prop, field, raw[field])

    prop.last_seen_at = datetime.now(timezone.utc)
    return prop, is_new


def match_criteria(prop, criteria):
    """Check whether `prop` satisfies `criteria`. Returns (matches, reasons)."""
    reasons = []

    if criteria.property_type_list and prop.property_type not in criteria.property_type_list:
        return False, []

    locations = [loc.lower() for loc in criteria.location_list]
    if locations:
        candidates = {(prop.city or "").lower(), (prop.zip_code or "").lower(), (prop.state or "").lower()}
        if not candidates & set(locations):
            return False, []
        reasons.append(f"Located in {prop.city}, {prop.state} — matches your target markets")

    zoning_codes = criteria.zoning_code_list
    if zoning_codes:
        prop_zoning = (prop.zoning_code or "").upper()
        if not any(prop_zoning.startswith(z.upper()) for z in zoning_codes):
            return False, []
        reasons.append(f"Zoning code {prop.zoning_code} matches your criteria")

    if criteria.min_price is not None and prop.list_price < criteria.min_price:
        return False, []
    if criteria.max_price is not None and prop.list_price > criteria.max_price:
        return False, []

    if criteria.min_cap_rate is not None:
        if (prop.cap_rate or 0) < criteria.min_cap_rate:
            return False, []
        reasons.append(f"Cap rate {prop.cap_rate:.1%} clears your {criteria.min_cap_rate:.1%} minimum")

    if criteria.min_lot_size_sqft is not None:
        if (prop.lot_size_sqft or 0) < criteria.min_lot_size_sqft:
            return False, []
        reasons.append(f"Lot size {prop.lot_size_sqft:,.0f} sqft meets your minimum")

    if criteria.strategy and criteria.strategy != "both":
        if prop.recommended_strategy != criteria.strategy:
            return False, []
        reasons.append(f"Fits your '{criteria.strategy.replace('_', ' ')}' strategy")

    reasons.extend(prop.reasons[:3])
    return True, reasons


def compute_match_score(prop, criteria):
    strategy_score = prop.buy_hold_score if criteria.strategy == "buy_and_hold" else (
        prop.development_score if criteria.strategy == "development" else max(prop.buy_hold_score, prop.development_score)
    )
    return round(strategy_score, 1)


def run_ingestion(cfg, sources=None):
    """Run one full ingestion + scoring + matching cycle.

    Returns a summary dict: {"fetched": int, "new": int, "updated": int, "recommendations": int}
    """
    sources = sources if sources is not None else get_configured_sources(cfg)

    fetched = 0
    new_count = 0
    touched_properties = []

    for source in sources:
        raw_listings = source.fetch_new_listings()
        fetched += len(raw_listings)
        for raw in raw_listings:
            prop, is_new = upsert_listing(raw)
            if is_new:
                new_count += 1
            touched_properties.append(prop)

    db.session.flush()

    for prop in touched_properties:
        score_property(prop, cfg)

    db.session.flush()

    active_criteria = Criteria.query.filter_by(active=True).all()
    new_recommendations = 0
    for prop in touched_properties:
        for criteria in active_criteria:
            matches, reasons = match_criteria(prop, criteria)
            if not matches:
                continue

            rec = Recommendation.query.filter_by(property_id=prop.id, criteria_id=criteria.id).first()
            if rec is None:
                rec = Recommendation(property_id=prop.id, criteria_id=criteria.id, status="new")
                db.session.add(rec)
                new_recommendations += 1
            rec.match_score = compute_match_score(prop, criteria)
            rec.reasons = reasons

    db.session.commit()

    return {
        "fetched": fetched,
        "new": new_count,
        "updated": len(touched_properties) - new_count,
        "recommendations": new_recommendations,
    }
