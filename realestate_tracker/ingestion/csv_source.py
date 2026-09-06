"""Import listings from a CSV file.

Useful for county assessor exports, a broker-provided spreadsheet, or a
manual export from a listing site. Expected columns (header row, any casing,
underscores or spaces both fine):

    external_id, address, city, state, zip_code, county, property_type,
    zoning_code, zoning_description, allowed_units, existing_units,
    lot_size_sqft, building_size_sqft, year_built, list_price,
    annual_property_tax, estimated_rent_monthly, hoa_monthly, status,
    listing_url, listed_date, latitude, longitude

Only external_id, address, city, state, zip_code, and list_price are
required -- everything else is optional and left blank/None if missing,
which will simply reduce the confidence of the scoring engine's output
(e.g. no cap rate without a rent estimate).
"""
import csv
from datetime import datetime

REQUIRED_FIELDS = {"external_id", "address", "city", "state", "zip_code", "list_price"}

FLOAT_FIELDS = {
    "list_price", "annual_property_tax", "estimated_rent_monthly", "hoa_monthly",
    "lot_size_sqft", "building_size_sqft", "latitude", "longitude",
}
INT_FIELDS = {"allowed_units", "existing_units", "year_built"}
DATE_FIELDS = {"listed_date"}

from .base import DataSource


def _normalize_key(key):
    return key.strip().lower().replace(" ", "_")


def _coerce(field, value):
    if value is None or value == "":
        return None
    if field in FLOAT_FIELDS:
        return float(value)
    if field in INT_FIELDS:
        return int(float(value))
    if field in DATE_FIELDS:
        for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        return None
    return value


class CSVDataSource(DataSource):
    name = "csv"

    def __init__(self, file_path):
        self.file_path = file_path

    def fetch_new_listings(self):
        listings = []
        with open(self.file_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row_num, raw_row in enumerate(reader, start=2):
                row = {_normalize_key(k): v for k, v in raw_row.items() if k}
                missing = REQUIRED_FIELDS - {k for k, v in row.items() if v}
                if missing:
                    raise ValueError(f"CSV row {row_num} missing required field(s): {sorted(missing)}")

                listing = {"source": self.name}
                for key, value in row.items():
                    listing[key] = _coerce(key, value)
                listing.setdefault("status", "active")
                listing.setdefault("property_type", "residential")
                listings.append(listing)
        return listings
