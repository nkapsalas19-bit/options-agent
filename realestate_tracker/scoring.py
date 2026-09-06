"""Turns a raw listing into the numbers an investor actually cares about:
NOI, cap rate, cash-on-cash return, and two 0-100 fit scores (buy-and-hold
vs. development) with human-readable reasons attached.

Nothing here talks to a database or an API -- it's pure functions over a
Property model instance so it's easy to unit test and reuse from both the
ingestion pipeline and the web UI.
"""
from dataclasses import dataclass, field


def _monthly_mortgage_payment(loan_amount, annual_rate, term_years):
    if loan_amount <= 0:
        return 0.0
    monthly_rate = annual_rate / 12
    n_payments = term_years * 12
    if monthly_rate == 0:
        return loan_amount / n_payments
    return loan_amount * (monthly_rate * (1 + monthly_rate) ** n_payments) / (
        (1 + monthly_rate) ** n_payments - 1
    )


@dataclass
class ScoreResult:
    noi_annual: float = 0.0
    cap_rate: float = 0.0
    cash_on_cash: float = 0.0
    monthly_cash_flow: float = 0.0
    price_per_sqft: float = 0.0
    price_per_unit: float = 0.0
    development_score: float = 0.0
    buy_hold_score: float = 0.0
    recommended_strategy: str = "buy_and_hold"
    reasons: list = field(default_factory=list)


def compute_noi(prop, cfg):
    """Annual Net Operating Income estimate from gross rent and expenses."""
    gross_rent_monthly = prop.estimated_rent_monthly or 0
    gross_annual_rent = gross_rent_monthly * 12
    effective_gross_income = gross_annual_rent * (1 - cfg.VACANCY_RATE)

    annual_tax = prop.annual_property_tax or 0
    insurance = gross_annual_rent * cfg.INSURANCE_PCT_OF_RENT
    maintenance = gross_annual_rent * cfg.MAINTENANCE_PCT_OF_RENT
    management = gross_annual_rent * cfg.MANAGEMENT_PCT_OF_RENT
    hoa = (prop.hoa_monthly or 0) * 12

    operating_expenses = annual_tax + insurance + maintenance + management + hoa
    return effective_gross_income - operating_expenses


def compute_financing(prop, cfg, noi_annual):
    """Cash-on-cash return and monthly cash flow given financing assumptions."""
    price = prop.list_price or 0
    down_payment = price * cfg.DOWN_PAYMENT_PCT
    closing_costs = price * cfg.CLOSING_COST_PCT
    loan_amount = price - down_payment

    monthly_payment = _monthly_mortgage_payment(loan_amount, cfg.INTEREST_RATE, cfg.LOAN_TERM_YEARS)
    annual_debt_service = monthly_payment * 12

    annual_cash_flow = noi_annual - annual_debt_service
    monthly_cash_flow = annual_cash_flow / 12

    total_cash_invested = down_payment + closing_costs
    cash_on_cash = annual_cash_flow / total_cash_invested if total_cash_invested else 0.0

    return cash_on_cash, monthly_cash_flow


def _clamp(value, low=0.0, high=100.0):
    return max(low, min(high, value))


def compute_buy_hold_score(cap_rate, cash_on_cash, status, cfg):
    reasons = []
    score = 0.0

    # Cap rate contributes up to 50 points, scaled against the target.
    if cfg.TARGET_CAP_RATE > 0:
        cap_component = _clamp((cap_rate / cfg.TARGET_CAP_RATE) * 50, 0, 60)
    else:
        cap_component = 0
    score += cap_component
    if cap_rate >= cfg.TARGET_CAP_RATE:
        reasons.append(
            f"Cap rate {cap_rate:.1%} meets or exceeds your {cfg.TARGET_CAP_RATE:.1%} target"
        )
    elif cap_rate > 0:
        reasons.append(
            f"Cap rate {cap_rate:.1%} is below the {cfg.TARGET_CAP_RATE:.1%} target"
        )

    # Cash-on-cash contributes up to 40 points.
    if cfg.TARGET_CASH_ON_CASH > 0:
        coc_component = _clamp((cash_on_cash / cfg.TARGET_CASH_ON_CASH) * 40, 0, 45)
    else:
        coc_component = 0
    score += coc_component
    if cash_on_cash >= cfg.TARGET_CASH_ON_CASH:
        reasons.append(f"Cash-on-cash return of {cash_on_cash:.1%} beats your target")
    elif cash_on_cash > 0:
        reasons.append(f"Positive but modest cash-on-cash return of {cash_on_cash:.1%}")
    else:
        reasons.append("Estimated to be cash-flow negative at listed price")

    # Small bonus for actively listed / immediately actionable deals.
    if status == "active":
        score += 5

    return _clamp(score), reasons


def compute_development_score(prop):
    reasons = []
    score = 0.0

    allowed = prop.allowed_units or 0
    existing = prop.existing_units or 1
    if allowed and existing and allowed > existing:
        upside_ratio = allowed / existing
        # Up to 50 points for zoning entitlement upside.
        zoning_component = _clamp((upside_ratio - 1) * 25, 0, 50)
        score += zoning_component
        reasons.append(
            f"Zoning ({prop.zoning_code or 'n/a'}) allows {allowed} units vs. "
            f"{existing} existing -- {upside_ratio:.1f}x density upside"
        )

    lot = prop.lot_size_sqft or 0
    building = prop.building_size_sqft or 0
    if lot > 0:
        # Low building-to-lot coverage suggests room to build / add units.
        coverage = building / lot if lot else 1.0
        if coverage < 0.3:
            coverage_component = _clamp((0.3 - coverage) / 0.3 * 25, 0, 25)
            score += coverage_component
            reasons.append(
                f"Low lot coverage ({coverage:.0%}) leaves room to expand or redevelop"
            )

    year_built = prop.year_built
    if year_built and year_built < 1970 and building > 0:
        score += 10
        reasons.append(f"Older structure (built {year_built}) -- possible teardown/renovation play")

    if prop.property_type in ("land", "mixed_use", "industrial"):
        score += 10
        reasons.append(f"Property type '{prop.property_type}' is commonly development-friendly")

    if not reasons:
        reasons.append("No clear zoning or lot-coverage upside detected")

    return _clamp(score), reasons


def score_property(prop, cfg):
    """Compute all derived financial fields and scores for a Property.

    Mutates and returns the same instance (caller is responsible for
    committing to the DB) so it can be used both during ingestion and for
    ad-hoc re-scoring in the UI/tests.
    """
    noi = compute_noi(prop, cfg)
    price = prop.list_price or 0
    cap_rate = noi / price if price else 0.0
    cash_on_cash, monthly_cash_flow = compute_financing(prop, cfg, noi)

    buy_hold_score, bh_reasons = compute_buy_hold_score(cap_rate, cash_on_cash, prop.status, cfg)
    dev_score, dev_reasons = compute_development_score(prop)

    if dev_score >= buy_hold_score and dev_score >= 40:
        recommended_strategy = "development"
    elif buy_hold_score >= 40:
        recommended_strategy = "buy_and_hold"
    else:
        recommended_strategy = "buy_and_hold" if buy_hold_score >= dev_score else "development"

    prop.noi_annual = noi
    prop.cap_rate = cap_rate
    prop.cash_on_cash = cash_on_cash
    prop.monthly_cash_flow = monthly_cash_flow
    prop.price_per_sqft = (price / prop.building_size_sqft) if prop.building_size_sqft else None
    prop.price_per_unit = (price / prop.existing_units) if prop.existing_units else None
    prop.development_score = dev_score
    prop.buy_hold_score = buy_hold_score
    prop.recommended_strategy = recommended_strategy
    prop.reasons = bh_reasons + dev_reasons

    return ScoreResult(
        noi_annual=noi,
        cap_rate=cap_rate,
        cash_on_cash=cash_on_cash,
        monthly_cash_flow=monthly_cash_flow,
        price_per_sqft=prop.price_per_sqft or 0.0,
        price_per_unit=prop.price_per_unit or 0.0,
        development_score=dev_score,
        buy_hold_score=buy_hold_score,
        recommended_strategy=recommended_strategy,
        reasons=prop.reasons,
    )
