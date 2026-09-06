import json
from datetime import datetime, timezone

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class JSONListMixin:
    """Store a Python list as a JSON text column, exposed via a plain attribute."""

    @staticmethod
    def dumps(value):
        return json.dumps(value or [])

    @staticmethod
    def loads(value):
        return json.loads(value) if value else []


PROPERTY_TYPES = [
    "residential",
    "multifamily",
    "commercial",
    "mixed_use",
    "industrial",
    "land",
]

STRATEGIES = ["buy_and_hold", "development", "both"]


class Property(db.Model):
    __tablename__ = "properties"

    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(50), nullable=False, default="sample")
    external_id = db.Column(db.String(120), nullable=False)

    # Location
    address = db.Column(db.String(255), nullable=False)
    city = db.Column(db.String(120), nullable=False)
    state = db.Column(db.String(2), nullable=False)
    zip_code = db.Column(db.String(10), nullable=False)
    county = db.Column(db.String(120))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)

    # Classification
    property_type = db.Column(db.String(30), nullable=False, default="residential")
    zoning_code = db.Column(db.String(30))
    zoning_description = db.Column(db.String(255))
    # Rough proxy for "what the zoning would allow" vs "what's built today".
    # >1 means there's unused development entitlement (upzone / teardown play).
    allowed_units = db.Column(db.Integer)
    existing_units = db.Column(db.Integer, default=1)

    # Physical characteristics
    lot_size_sqft = db.Column(db.Float)
    building_size_sqft = db.Column(db.Float)
    year_built = db.Column(db.Integer)

    # Financials (inputs)
    list_price = db.Column(db.Float, nullable=False)
    annual_property_tax = db.Column(db.Float)
    estimated_rent_monthly = db.Column(db.Float)
    hoa_monthly = db.Column(db.Float, default=0)

    # Listing metadata
    status = db.Column(db.String(20), default="active")  # active/pending/sold/off_market
    listing_url = db.Column(db.String(500))
    listed_date = db.Column(db.Date)
    first_seen_at = db.Column(db.DateTime, default=utcnow)
    last_seen_at = db.Column(db.DateTime, default=utcnow)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    # Financials (computed by scoring.py -- persisted so the dashboard can
    # filter/sort in SQL without recomputing on every request)
    noi_annual = db.Column(db.Float)
    cap_rate = db.Column(db.Float)
    cash_on_cash = db.Column(db.Float)
    monthly_cash_flow = db.Column(db.Float)
    price_per_sqft = db.Column(db.Float)
    price_per_unit = db.Column(db.Float)
    development_score = db.Column(db.Float, default=0)
    buy_hold_score = db.Column(db.Float, default=0)
    recommended_strategy = db.Column(db.String(20))
    score_reasons = db.Column(db.Text)  # JSON list[str]

    __table_args__ = (
        db.UniqueConstraint("source", "external_id", name="uq_property_source_external_id"),
    )

    @property
    def reasons(self):
        return JSONListMixin.loads(self.score_reasons)

    @reasons.setter
    def reasons(self, value):
        self.score_reasons = JSONListMixin.dumps(value)

    def to_dict(self):
        return {
            "id": self.id,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "zip_code": self.zip_code,
            "property_type": self.property_type,
            "zoning_code": self.zoning_code,
            "list_price": self.list_price,
            "annual_property_tax": self.annual_property_tax,
            "cap_rate": self.cap_rate,
            "cash_on_cash": self.cash_on_cash,
            "monthly_cash_flow": self.monthly_cash_flow,
            "development_score": self.development_score,
            "buy_hold_score": self.buy_hold_score,
            "recommended_strategy": self.recommended_strategy,
            "status": self.status,
            "listing_url": self.listing_url,
        }


class Criteria(db.Model):
    """A saved investor profile / search the recommendation engine matches
    incoming properties against."""

    __tablename__ = "criteria"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)

    locations = db.Column(db.Text)  # JSON list of city or zip strings, case-insensitive match
    property_types = db.Column(db.Text)  # JSON list, subset of PROPERTY_TYPES
    zoning_codes = db.Column(db.Text)  # JSON list, optional prefix match (e.g. "MU", "R")

    min_price = db.Column(db.Float)
    max_price = db.Column(db.Float)
    min_cap_rate = db.Column(db.Float)
    min_lot_size_sqft = db.Column(db.Float)  # useful for development-focused criteria
    strategy = db.Column(db.String(20), default="both")

    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    @property
    def location_list(self):
        return JSONListMixin.loads(self.locations)

    @location_list.setter
    def location_list(self, value):
        self.locations = JSONListMixin.dumps(value)

    @property
    def property_type_list(self):
        return JSONListMixin.loads(self.property_types)

    @property_type_list.setter
    def property_type_list(self, value):
        self.property_types = JSONListMixin.dumps(value)

    @property
    def zoning_code_list(self):
        return JSONListMixin.loads(self.zoning_codes)

    @zoning_code_list.setter
    def zoning_code_list(self, value):
        self.zoning_codes = JSONListMixin.dumps(value)


class Recommendation(db.Model):
    """A property that matched an active Criteria profile."""

    __tablename__ = "recommendations"

    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(db.Integer, db.ForeignKey("properties.id"), nullable=False)
    criteria_id = db.Column(db.Integer, db.ForeignKey("criteria.id"), nullable=False)

    match_score = db.Column(db.Float, default=0)
    reasons_json = db.Column(db.Text)
    status = db.Column(db.String(20), default="new")  # new/viewed/saved/dismissed
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    listing = db.relationship("Property", backref="recommendations")
    criteria = db.relationship("Criteria", backref="recommendations")

    __table_args__ = (
        db.UniqueConstraint("property_id", "criteria_id", name="uq_recommendation_property_criteria"),
    )

    @property
    def reasons(self):
        return JSONListMixin.loads(self.reasons_json)

    @reasons.setter
    def reasons(self, value):
        self.reasons_json = JSONListMixin.dumps(value)
