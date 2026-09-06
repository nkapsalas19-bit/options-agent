from ..extensions import db
from ..ingestion.base import DataSource
from ..ingestion.pipeline import run_ingestion
from ..models import Criteria, Property, Recommendation


class FixedDataSource(DataSource):
    name = "fixed"

    def __init__(self, listings):
        self._listings = listings

    def fetch_new_listings(self):
        return self._listings


def make_listing(**overrides):
    listing = dict(
        source="fixed",
        external_id="p1",
        address="1 Main St",
        city="Austin",
        state="TX",
        zip_code="78701",
        property_type="multifamily",
        zoning_code="R3",
        allowed_units=6,
        existing_units=2,
        lot_size_sqft=8000,
        building_size_sqft=2500,
        year_built=1985,
        list_price=500_000,
        annual_property_tax=6000,
        estimated_rent_monthly=5000,
        hoa_monthly=0,
        status="active",
    )
    listing.update(overrides)
    return listing


def test_ingestion_creates_property(app):
    with app.app_context():
        summary = run_ingestion(app.re_config, sources=[FixedDataSource([make_listing()])])
        assert summary["fetched"] == 1
        assert summary["new"] == 1
        assert Property.query.count() == 1


def test_ingestion_upserts_existing_property(app):
    with app.app_context():
        source = FixedDataSource([make_listing()])
        run_ingestion(app.re_config, sources=[source])
        run_ingestion(app.re_config, sources=[FixedDataSource([make_listing(list_price=550_000)])])
        assert Property.query.count() == 1
        prop = Property.query.first()
        assert prop.list_price == 550_000


def test_matching_criteria_creates_recommendation(app):
    with app.app_context():
        criteria = Criteria(name="Austin multifamily", strategy="both", min_cap_rate=0.01)
        criteria.location_list = ["Austin"]
        criteria.property_type_list = ["multifamily"]
        db.session.add(criteria)
        db.session.commit()

        run_ingestion(app.re_config, sources=[FixedDataSource([make_listing()])])

        recs = Recommendation.query.all()
        assert len(recs) == 1
        assert recs[0].criteria_id == criteria.id
        assert recs[0].status == "new"
        assert len(recs[0].reasons) > 0


def test_non_matching_location_produces_no_recommendation(app):
    with app.app_context():
        criteria = Criteria(name="Miami only", strategy="both")
        criteria.location_list = ["Miami"]
        db.session.add(criteria)
        db.session.commit()

        run_ingestion(app.re_config, sources=[FixedDataSource([make_listing(city="Austin")])])

        assert Recommendation.query.count() == 0


def test_inactive_criteria_is_ignored(app):
    with app.app_context():
        criteria = Criteria(name="Paused search", strategy="both", active=False)
        criteria.location_list = ["Austin"]
        db.session.add(criteria)
        db.session.commit()

        run_ingestion(app.re_config, sources=[FixedDataSource([make_listing()])])

        assert Recommendation.query.count() == 0
