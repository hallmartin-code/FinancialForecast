"""Deterministic `ModelAssumptions` builders.

The modelling tests must not depend on a cached API extraction, so these construct
assumptions directly with round numbers chosen to make hand-checked arithmetic
possible.
"""

from __future__ import annotations

from typing import Any

from pitchdeck_cfo.assume.schema import (
    COGSAssumptions,
    CompanyMeta,
    FinancingAssumptions,
    Function,
    HardwareRevenue,
    HeadcountAssumptions,
    LifeSciencesRevenue,
    ModelAssumptions,
    OpexAssumptions,
    ProgramPhase,
    SaaSRevenue,
    TaxAssumptions,
    WorkingCapitalAssumptions,
)
from pitchdeck_cfo.model.headcount import FUNCTIONS
from pitchdeck_cfo.models import BusinessModelKind, CoverageReport, Sourced


def s(value: float, source: str = "derived") -> Sourced[float]:
    citation = None if source == "derived" else "test"
    return Sourced[float](value=value, source=source, citation=citation)  # type: ignore[arg-type]


def company(model: BusinessModelKind = "saas", **kwargs: Any) -> CompanyMeta:
    fields: dict[str, Any] = {
        "name": "Testco",
        "business_model": model,
        "sector": None,
        "currency": "USD",
        "start_month": "2026-01",
        "horizon_years": 5,
        "as_of_basis": Sourced[str](value="test", source="derived"),
    }
    fields.update(kwargs)
    return CompanyMeta(**fields)


def saas_revenue(**kwargs: Any) -> SaaSRevenue:
    fields: dict[str, Any] = {
        "starting_arr": s(1_200_000),
        "starting_customers": s(100),
        "arpu_annual": s(12_000),
        "new_logos_month_1": s(10),
        "new_logo_growth_monthly_pct": s(0.0),
        "growth_decay_annual_pct": s(0.0),
        "terminal_growth_monthly_pct": s(0.0),
        "logo_churn_annual_pct": s(0.0),
        "net_revenue_retention_pct": s(100.0),
        "annual_prepay_mix_pct": s(0.0),
    }
    fields.update(kwargs)
    return SaaSRevenue(**fields)


def hardware_revenue(**kwargs: Any) -> HardwareRevenue:
    fields: dict[str, Any] = {
        "device_asp": s(4_000),
        "units_month_1": s(10),
        "unit_growth_monthly_pct": s(0.0),
        "growth_decay_annual_pct": s(0.0),
        "terminal_growth_monthly_pct": s(0.0),
        "consumable_price": s(30),
        "consumables_per_device_per_year": s(12),
        "installed_base_start": s(0),
        "direct_sales_mix_pct": s(100.0),
        "distributor_margin_pct": s(0.0),
    }
    fields.update(kwargs)
    return HardwareRevenue(**fields)


def life_sciences_revenue(**kwargs: Any) -> LifeSciencesRevenue:
    fields: dict[str, Any] = {
        "program_phases": (
            ProgramPhase(
                name="IND-enabling",
                start_month_offset=s(0),
                duration_months=s(12),
                total_cost=s(1_200_000),
            ),
        ),
        "non_dilutive_funding": s(0),
        "partnership_upfront": s(0),
        "royalty_rate_pct": s(0),
    }
    fields.update(kwargs)
    return LifeSciencesRevenue(**fields)


def cogs(**kwargs: Any) -> COGSAssumptions:
    fields: dict[str, Any] = {
        "hosting_pct_of_revenue": s(0.0),
        "hosting_scale_exponent": s(1.0),
        "support_pct_of_revenue": s(0.0),
        "payment_processing_pct": s(0.0),
        "bom_pct_of_asp": s(0.0),
        "consumable_gross_margin_pct": s(0.0),
        "warranty_pct_of_revenue": s(0.0),
        "target_gross_margin_pct": s(0.0),
    }
    fields.update(kwargs)
    return COGSAssumptions(**fields)


def headcount(starting: dict[str, float] | None = None, **kwargs: Any) -> HeadcountAssumptions:
    counts = starting or {}
    fields: dict[str, Any] = {
        "starting": {f: s(counts.get(f, 0.0)) for f in FUNCTIONS},
        "base_salary": {f: s(120_000) for f in FUNCTIONS},
        "loaded_multiplier": s(1.0),
        "annual_attrition_pct": s(0.0),
        "rep_quota_annual": s(0.0),
        "rep_ramp_months": s(0.0),
        "quota_attainment_pct": s(100.0),
        "accounts_per_csm": s(0.0),
        "engineers_per_product_line": s(0.0),
        "product_lines": s(0.0),
        "revenue_per_engineer": s(0.0),
        "sales_per_marketing_hire": s(0.0),
        "ftes_per_ga_hire": s(0.0),
        "scale_exponent": s(1.0),
    }
    fields.update(kwargs)
    typed: dict[Function, Any] = fields["starting"]
    _ = typed
    return HeadcountAssumptions(**fields)


def opex(**kwargs: Any) -> OpexAssumptions:
    fields: dict[str, Any] = {
        "marketing_pct_of_new_revenue": s(0.0),
        "rd_tooling_per_engineer": s(0.0),
        "rent_per_fte": s(0.0),
        "software_per_fte": s(0.0),
        "ga_fixed_annual": s(0.0),
    }
    fields.update(kwargs)
    return OpexAssumptions(**fields)


def working_capital(**kwargs: Any) -> WorkingCapitalAssumptions:
    fields: dict[str, Any] = {
        "dso_days": s(0.0),
        "dpo_days": s(0.0),
        "inventory_days": s(0.0),
        "capex_pct_of_revenue": s(0.0),
        "capex_depreciation_years": s(3.0),
    }
    fields.update(kwargs)
    return WorkingCapitalAssumptions(**fields)


def financing(**kwargs: Any) -> FinancingAssumptions:
    fields: dict[str, Any] = {
        "cash_on_hand": s(0.0),
        "raise_amount": s(0.0),
        "raise_month_offset": s(0.0),
        "monthly_burn_at_start": s(0.0),
    }
    fields.update(kwargs)
    return FinancingAssumptions(**fields)


def tax(**kwargs: Any) -> TaxAssumptions:
    fields: dict[str, Any] = {"blended_rate_pct": s(0.0), "nol_carryforward_start": s(0.0)}
    fields.update(kwargs)
    return TaxAssumptions(**fields)


def assumptions(model: BusinessModelKind = "saas", **overrides: Any) -> ModelAssumptions:
    """A complete, deterministic set of assumptions with everything switched off.

    Every rate defaults to zero so a test can turn on exactly the one mechanism it is
    checking and hand-verify the arithmetic.
    """
    revenue_by_model = {
        "saas": saas_revenue,
        "hardware": hardware_revenue,
        "life_sciences": life_sciences_revenue,
    }
    fields: dict[str, Any] = {
        "company": company(model),
        "revenue": revenue_by_model[model](),
        "cogs": cogs(),
        "headcount": headcount(),
        "opex": opex(),
        "working_capital": working_capital(),
        "financing": financing(),
        "tax": tax(),
        "coverage": CoverageReport(core_inputs=()),
    }
    fields.update(overrides)
    return ModelAssumptions(**fields)
