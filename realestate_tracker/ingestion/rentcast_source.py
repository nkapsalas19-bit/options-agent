"""Real-data connector template for RentCast (https://www.rentcast.io/api).

RentCast was picked as the reference "real" integration because, unlike
Zillow/Redfin/most MLS boards, it sells API access (no brokerage
membership required) and its property-records + rent-estimate + active
listings endpoints line up well with what the scoring engine needs (list
price, tax history, rent estimate). It is NOT free -- you need your own
API key (RENTCAST_API_KEY env var) and plan that covers the endpoints
below.

This is a best-effort mapping and may need small adjustments if RentCast
changes their response schema -- treat it as a starting template, verify
field names against your account's live API responses, and adjust
_map_listing() accordingly. If RENTCAST_API_KEY is not set, the pipeline
should skip this source entirely rather than fail (see ingestion/pipeline.py).
"""
import requests

from .base import DataSource

API_BASE_URL = "https://api.rentcast.io/v1"


class RentCastDataSource(DataSource):
    name = "rentcast"

    def __init__(self, api_key, cities=None, limit=20):
        if not api_key:
            raise ValueError("RentCastDataSource requires an API key")
        self.api_key = api_key
        # e.g. [{"city": "Austin", "state": "TX"}, ...] -- limits the search
        # to markets you actually invest in instead of pulling nationwide.
        self.cities = cities or []
        self.limit = limit

    def _headers(self):
        return {"X-Api-Key": self.api_key, "Accept": "application/json"}

    def _map_listing(self, raw):
        return {
            "source": self.name,
            "external_id": str(raw.get("id") or raw.get("formattedAddress")),
            "address": raw.get("addressLine1") or raw.get("formattedAddress"),
            "city": raw.get("city"),
            "state": raw.get("state"),
            "zip_code": raw.get("zipCode"),
            "county": raw.get("county"),
            "latitude": raw.get("latitude"),
            "longitude": raw.get("longitude"),
            "property_type": self._map_property_type(raw.get("propertyType")),
            "zoning_code": raw.get("zoning"),
            "zoning_description": None,
            "allowed_units": None,
            "existing_units": raw.get("unitCount") or 1,
            "lot_size_sqft": raw.get("lotSize"),
            "building_size_sqft": raw.get("squareFootage"),
            "year_built": raw.get("yearBuilt"),
            "list_price": raw.get("price") or raw.get("listPrice"),
            "annual_property_tax": self._latest_tax(raw.get("taxAssessments")),
            "estimated_rent_monthly": raw.get("rentEstimate") or raw.get("rent"),
            "hoa_monthly": (raw.get("hoa") or {}).get("fee") if isinstance(raw.get("hoa"), dict) else None,
            "status": raw.get("status", "active"),
            "listing_url": raw.get("listingUrl") or raw.get("url"),
            "listed_date": None,
        }

    @staticmethod
    def _map_property_type(rentcast_type):
        if not rentcast_type:
            return "residential"
        t = rentcast_type.lower()
        if "multi" in t or "apartment" in t:
            return "multifamily"
        if "commercial" in t or "retail" in t or "office" in t:
            return "commercial"
        if "industrial" in t or "warehouse" in t:
            return "industrial"
        if "land" in t or "lot" in t:
            return "land"
        if "mixed" in t:
            return "mixed_use"
        return "residential"

    @staticmethod
    def _latest_tax(tax_assessments):
        if not tax_assessments:
            return None
        try:
            latest_year = max(tax_assessments.keys())
            return tax_assessments[latest_year].get("value")
        except (AttributeError, ValueError):
            return None

    def fetch_new_listings(self):
        listings = []
        markets = self.cities or [{}]  # empty dict = unfiltered search, if your plan allows it
        for market in markets:
            params = {"limit": self.limit, "status": "Active"}
            params.update(market)
            resp = requests.get(
                f"{API_BASE_URL}/listings/sale",
                headers=self._headers(),
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            for raw in resp.json():
                listings.append(self._map_listing(raw))
        return listings
