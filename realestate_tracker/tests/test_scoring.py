from ..config import Config
from ..models import Property
from ..scoring import score_property


def make_property(**overrides):
    defaults = dict(
        source="test",
        external_id="1",
        address="1 Test St",
        city="Testville",
        state="TX",
        zip_code="00000",
        property_type="multifamily",
        zoning_code="R3",
        allowed_units=4,
        existing_units=2,
        lot_size_sqft=6000,
        building_size_sqft=2000,
        year_built=1990,
        list_price=400_000,
        annual_property_tax=5000,
        estimated_rent_monthly=4000,
        hoa_monthly=0,
        status="active",
    )
    defaults.update(overrides)
    return Property(**defaults)


def test_cap_rate_is_positive_for_healthy_rent_to_price_ratio():
    prop = make_property()
    result = score_property(prop, Config)
    assert result.cap_rate > 0
    assert prop.cap_rate == result.cap_rate


def test_higher_rent_increases_cap_rate():
    low_rent = make_property(estimated_rent_monthly=2000)
    high_rent = make_property(estimated_rent_monthly=5000)
    score_property(low_rent, Config)
    score_property(high_rent, Config)
    assert high_rent.cap_rate > low_rent.cap_rate


def test_zoning_upside_drives_development_score():
    no_upside = make_property(allowed_units=2, existing_units=2)
    with_upside = make_property(allowed_units=8, existing_units=2)
    score_property(no_upside, Config)
    score_property(with_upside, Config)
    assert with_upside.development_score > no_upside.development_score


def test_zero_rent_does_not_error_and_yields_non_positive_cap_rate():
    prop = make_property(estimated_rent_monthly=None, property_type="land")
    result = score_property(prop, Config)
    # No rental income but taxes still apply -> NOI can't be positive.
    assert result.cap_rate <= 0
    assert prop.recommended_strategy in ("buy_and_hold", "development")


def test_recommended_strategy_matches_higher_score():
    prop = make_property(allowed_units=10, existing_units=1, estimated_rent_monthly=100)
    score_property(prop, Config)
    if prop.development_score > prop.buy_hold_score:
        assert prop.recommended_strategy == "development"


def test_reasons_are_populated():
    prop = make_property()
    score_property(prop, Config)
    assert len(prop.reasons) > 0
