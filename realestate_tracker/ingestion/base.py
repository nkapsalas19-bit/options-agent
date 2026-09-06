"""Data source interface.

A DataSource's only job is to return a list of raw listing dicts. The
pipeline (ingestion/pipeline.py) takes care of upserting them into the
Property table and scoring them, so a new source only needs to implement
`fetch_new_listings()` and map its fields onto this common shape:

    {
        "source": "rentcast",              # short name, used with external_id for dedup
        "external_id": "abc123",           # stable id from the source
        "address": "123 Main St",
        "city": "Springfield",
        "state": "IL",
        "zip_code": "62701",
        "county": "Sangamon",              # optional
        "latitude": 39.78, "longitude": -89.65,   # optional
        "property_type": "multifamily",    # residential/multifamily/commercial/mixed_use/industrial/land
        "zoning_code": "R3",               # optional
        "zoning_description": "Multi-family residential",  # optional
        "allowed_units": 4,                # optional, zoning entitlement
        "existing_units": 2,               # optional, defaults to 1
        "lot_size_sqft": 6000,             # optional
        "building_size_sqft": 2200,        # optional
        "year_built": 1958,                # optional
        "list_price": 350000,
        "annual_property_tax": 4200,       # optional but strongly recommended
        "estimated_rent_monthly": 3200,    # optional but required for cap rate math
        "hoa_monthly": 0,                  # optional
        "status": "active",
        "listing_url": "https://...",      # optional
        "listed_date": date(2026, 9, 1),   # optional
    }

Any field not provided is left as None/default on the Property row.
"""
from abc import ABC, abstractmethod


class DataSource(ABC):
    name = "base"

    @abstractmethod
    def fetch_new_listings(self):
        """Return a list of raw listing dicts (see module docstring)."""
        raise NotImplementedError
