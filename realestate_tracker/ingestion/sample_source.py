"""Synthetic listing generator.

There is no free, public "new listings" feed for real estate (Zillow/Redfin/
MLS all require paid or brokerage-only access) so this source lets the
platform be fully usable out of the box: every ingestion run simulates a
handful of new listings "coming on the market" across a configurable set of
markets, with realistic price/rent/zoning relationships so the scoring
engine has something meaningful to chew on.

Swap this out for CSVDataSource or RentCastDataSource (see csv_source.py /
rentcast_source.py) once you have a real feed -- the pipeline doesn't care
which source produced a listing.
"""
import random
import uuid
from datetime import date

from .base import DataSource

MARKETS = [
    ("Austin", "TX", "78701", "Travis"),
    ("Raleigh", "NC", "27601", "Wake"),
    ("Tampa", "FL", "33602", "Hillsborough"),
    ("Columbus", "OH", "43215", "Franklin"),
    ("Phoenix", "AZ", "85004", "Maricopa"),
    ("Kansas City", "MO", "64105", "Jackson"),
    ("Indianapolis", "IN", "46204", "Marion"),
]

STREET_NAMES = ["Oak", "Maple", "Cedar", "Elm", "Washington", "Lincoln", "Sunset", "River", "Park", "Highland"]
STREET_SUFFIXES = ["St", "Ave", "Dr", "Ln", "Blvd", "Ct"]

# (property_type, zoning_code, zoning_description, price_range, rent_yield_range, weight)
PROPERTY_PROFILES = [
    (
        "residential", "R1", "Single-family residential",
        (180_000, 420_000), (0.006, 0.009), 3,
    ),
    (
        "multifamily", "R3", "Multi-family residential (up to 4 units)",
        (300_000, 850_000), (0.008, 0.012), 3,
    ),
    (
        "mixed_use", "MU2", "Mixed-use, ground-floor commercial",
        (450_000, 1_400_000), (0.007, 0.011), 2,
    ),
    (
        "commercial", "C2", "General commercial",
        (400_000, 2_000_000), (0.007, 0.010), 2,
    ),
    (
        "industrial", "I1", "Light industrial",
        (350_000, 1_800_000), (0.006, 0.009), 1,
    ),
    (
        "land", "R2", "Two-family residential (vacant parcel)",
        (60_000, 300_000), (0.0, 0.0), 1,
    ),
]


class SampleDataSource(DataSource):
    name = "sample"

    def __init__(self, count=3, seed=None):
        self.count = count
        self._rng = random.Random(seed)

    def _random_address(self):
        number = self._rng.randint(100, 9999)
        street = self._rng.choice(STREET_NAMES)
        suffix = self._rng.choice(STREET_SUFFIXES)
        return f"{number} {street} {suffix}"

    def _build_listing(self):
        rng = self._rng
        city, state, zip_code, county = rng.choice(MARKETS)
        ptype, zoning, zoning_desc, price_range, yield_range, _weight = rng.choices(
            PROPERTY_PROFILES, weights=[p[-1] for p in PROPERTY_PROFILES]
        )[0]

        list_price = round(rng.uniform(*price_range), -3)
        is_land = ptype == "land"

        monthly_rent = 0 if is_land else round(list_price * rng.uniform(*yield_range) / 12, -1)
        lot_size = round(rng.uniform(3000, 20000), -2) if ptype != "commercial" else round(rng.uniform(8000, 60000), -2)
        building_size = 0 if is_land else round(rng.uniform(900, 6000), -2)
        year_built = 0 if is_land else rng.randint(1920, 2022)

        existing_units = 1
        allowed_units = None
        if ptype == "multifamily":
            existing_units = rng.choice([2, 3, 4])
            allowed_units = existing_units + rng.choice([0, 0, 1, 2])
        elif ptype in ("mixed_use", "commercial"):
            existing_units = 1
            allowed_units = rng.choice([1, 2, 3])
        elif ptype == "residential":
            existing_units = 1
            allowed_units = rng.choice([1, 1, 2])
        elif ptype == "land":
            existing_units = 0
            allowed_units = rng.choice([2, 4, 6, 8])

        annual_tax = round(list_price * rng.uniform(0.008, 0.022))

        return {
            "source": self.name,
            "external_id": uuid.uuid4().hex[:16],
            "address": self._random_address(),
            "city": city,
            "state": state,
            "zip_code": zip_code,
            "county": county,
            "property_type": ptype,
            "zoning_code": zoning,
            "zoning_description": zoning_desc,
            "allowed_units": allowed_units,
            "existing_units": existing_units,
            "lot_size_sqft": lot_size,
            "building_size_sqft": building_size,
            "year_built": year_built or None,
            "list_price": list_price,
            "annual_property_tax": annual_tax,
            "estimated_rent_monthly": monthly_rent or None,
            "hoa_monthly": 0,
            "status": "active",
            "listing_url": None,
            "listed_date": date.today(),
        }

    def fetch_new_listings(self):
        return [self._build_listing() for _ in range(self.count)]
