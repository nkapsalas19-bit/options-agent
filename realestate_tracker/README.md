# Real Estate Deal Tracker

A platform for tracking real estate as it comes on the market and
recommending acquisitions, filtered/scored by **location, zoning, taxes,
cap rate, and property type** (residential, multifamily, commercial,
mixed-use, industrial, land) — for both **buy-and-hold** and
**development** strategies.

## How it works

```
DataSource(s)  --->  ingestion pipeline  --->  scoring engine  --->  matching engine  --->  dashboard
 (new listings)      (upsert Property)         (cap rate, NOI,       (vs. saved            (recommendations,
                                                 cash-on-cash,         Criteria profiles)     filters, saved
                                                 dev/buy-hold fit)                             searches)
```

1. **Data sources** (`ingestion/`) fetch raw listings. Three are included:
   - `sample_source.py` — generates realistic synthetic "new to market"
     listings across 7 sample metros so the app is useful with zero setup.
   - `csv_source.py` — imports a CSV export (county assessor data, a
     broker's spreadsheet, a manual export from a listing site).
   - `rentcast_source.py` — a real API connector template for
     [RentCast](https://www.rentcast.io/api), which (unlike Zillow/Redfin/
     most MLS boards) sells API access without requiring brokerage
     membership. Requires your own `RENTCAST_API_KEY`; auto-enables once
     that env var is set. Treat the field mapping as a starting template —
     verify it against your account's live responses.

   **Why not live Zillow/Redfin/MLS data out of the box?** Those platforms
   don't offer a free public "new listings" API — Zillow and Redfin have no
   public listings API at all, and MLS data requires an IDX/broker
   agreement. To go fully live, either plug in a paid data provider
   (RentCast, ATTOM, Estated, BatchLeads, etc. — subclass `DataSource`,
   see `ingestion/base.py`) or set up an IDX feed through a licensed broker
   and write a connector for it.

2. **Scoring engine** (`scoring.py`) turns a listing into the numbers that
   actually matter:
   - **NOI** = effective gross rent (after vacancy) − taxes − insurance −
     maintenance − management − HOA
   - **Cap rate** = NOI / list price
   - **Cash-on-cash return** and **monthly cash flow**, using configurable
     financing assumptions (down payment %, interest rate, loan term)
   - **Buy-and-hold score** (0–100) — how well cap rate and cash-on-cash
     clear your targets
   - **Development score** (0–100) — zoning entitlement upside (allowed
     units vs. existing units), low lot coverage, older structures, and
     development-friendly property types
   - A recommended strategy (`buy_and_hold` / `development`) plus
     human-readable reasons for both

   All assumptions (vacancy rate, expense ratios, financing terms, target
   cap rate / cash-on-cash) live in `config.py` and can be overridden via
   environment variables.

3. **Matching engine** (`ingestion/pipeline.py`) checks every touched
   property against every active **Criteria** (a saved investor profile:
   locations, property types, zoning codes, price range, minimum cap rate,
   minimum lot size, strategy). A match creates/updates a
   `Recommendation` with a match score and reasons — this is what "shows
   up and recommends" a deal to you.

4. **Dashboard** (Flask + server-rendered templates) lists recommendations,
   filterable by location, property type, zoning, min cap rate, max price,
   and strategy; sortable by match score, cap rate, price, or recency.
   Each property has a detail page with the full financial breakdown and
   scoring rationale. `/criteria` manages saved search profiles.

## Running it

```bash
cd realestate_tracker
pip install -r requirements.txt

# one-time: create tables, seed a few sample saved searches + listings
python -m realestate_tracker.cli seed --listings 20

# run the web app (manual "Check for new listings" button, no background job)
python -m realestate_tracker.cli serve --debug
```

Then open http://127.0.0.1:5050.

To run with the background scheduler enabled (periodic auto-ingestion
instead of manual button clicks):

```bash
python -m realestate_tracker.run
```

Ingestion cadence is controlled by `INGEST_INTERVAL_MINUTES` (default 60).

### Enabling real data

```bash
export RENTCAST_API_KEY=your_key_here
python -m realestate_tracker.cli ingest
```

Or import a CSV manually:

```python
from realestate_tracker.app import create_app
from realestate_tracker.ingestion.pipeline import run_ingestion
from realestate_tracker.ingestion.csv_source import CSVDataSource

app = create_app()
with app.app_context():
    run_ingestion(app.re_config, sources=[CSVDataSource("my_listings.csv")])
```

### Tuning the financial assumptions

Set any of these env vars before running (defaults shown):

| Variable | Default | Meaning |
|---|---|---|
| `VACANCY_RATE` | 0.05 | Assumed vacancy loss |
| `INSURANCE_PCT_OF_RENT` | 0.04 | Insurance as % of gross rent |
| `MAINTENANCE_PCT_OF_RENT` | 0.08 | Maintenance reserve as % of gross rent |
| `MANAGEMENT_PCT_OF_RENT` | 0.08 | Property management as % of gross rent |
| `DOWN_PAYMENT_PCT` | 0.25 | Assumed down payment |
| `INTEREST_RATE` | 0.07 | Assumed mortgage rate |
| `LOAN_TERM_YEARS` | 30 | Assumed amortization |
| `CLOSING_COST_PCT` | 0.03 | Closing costs as % of price |
| `TARGET_CAP_RATE` | 0.06 | Threshold for a "good" buy-and-hold cap rate |
| `TARGET_CASH_ON_CASH` | 0.08 | Threshold for a "good" cash-on-cash return |

## Tests

```bash
pip install pytest
python -m pytest realestate_tracker/tests -q
```

## Known limitations / next steps

- No live MLS/Zillow/Redfin feed by default (see above) — the sample
  source is for demoing the scoring/matching/UI end-to-end.
- No authentication — this is a single-user local tool as built. Add
  Flask-Login (or similar) before exposing it beyond your own machine.
- No map view yet, though `latitude`/`longitude` are captured on every
  property for a future map-based UI.
- Development scoring uses simple heuristics (zoning entitlement ratio,
  lot coverage, building age) rather than parcel-level GIS/zoning-ordinance
  data — good for triage, not a substitute for a zoning attorney or civil
  engineer before committing capital.
