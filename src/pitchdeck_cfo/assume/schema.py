"""`ModelAssumptions` -- the complete, provenance-tagged input to the modelling engine.

Every leaf is a `Sourced[float]`, so by the time a number reaches `model/` it already
knows whether it came from the deck, a benchmark, an arithmetic derivation, or the
user's own override file. The engine never asks where a number came from; it cannot
receive one that does not say.

Placed in `assume/` rather than the shared `models.py` for the same reason
`extract/schema.py` is: it is the contract of one stage, not of the pipeline.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pitchdeck_cfo.models import (
    BusinessModelKind,
    CoverageReport,
    Provenance,
    Sourced,
)

Money = Sourced[float]

Function = Literal[
    "Engineering",
    "Product",
    "Design",
    "Sales",
    "Marketing",
    "Customer Success",
    "G&A",
    "Clinical",
    "Regulatory",
    "Quality",
    "Manufacturing",
    "Research",
]


class CompanyMeta(BaseModel):
    """Identity and calendar. Everything else is measured against `start_month`."""

    model_config = ConfigDict(frozen=True)

    name: str
    business_model: BusinessModelKind
    sector: str | None
    currency: str = "USD"

    start_month: str = Field(description="First month of Year 1, as YYYY-MM.")
    horizon_years: int
    as_of_basis: Sourced[str] = Field(
        description="Why the calendar starts where it does -- deck-stated date or run date."
    )


# --------------------------------------------------------------------------- #
# revenue -- one variant per supported engine
# --------------------------------------------------------------------------- #


class SaaSRevenue(BaseModel):
    """Cohort build: new logos -> ARPU -> gross new ARR, less churn, plus expansion."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["saas"] = "saas"

    starting_arr: Money
    starting_customers: Sourced[float]
    arpu_annual: Money

    new_logos_month_1: Sourced[float]
    new_logo_growth_monthly_pct: Sourced[float]

    logo_churn_annual_pct: Sourced[float]
    net_revenue_retention_pct: Sourced[float]

    annual_prepay_mix_pct: Sourced[float] = Field(
        description="Share billed annually up front. Drives deferred revenue, not revenue."
    )


class LifeSciencesRevenue(BaseModel):
    """Milestone-driven. Usually no product revenue inside the horizon at all."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["life_sciences"] = "life_sciences"

    program_phases: tuple[ProgramPhase, ...]
    non_dilutive_funding: Money
    partnership_upfront: Money = Field(
        description="Contracted upfront only. Zero unless the deck states a signed deal."
    )
    royalty_rate_pct: Sourced[float]


class ProgramPhase(BaseModel):
    """One development stage: what it costs, how long it runs, what it unlocks."""

    model_config = ConfigDict(frozen=True)

    name: str
    start_month_offset: Sourced[float]
    duration_months: Sourced[float]
    total_cost: Money


class HardwareRevenue(BaseModel):
    """Units x ASP for the device, plus a recurring consumable at its own attach rate.

    The razor/razor-blade shape. Modelling a device business without the consumable
    stream understates it badly; modelling it without the distributor haircut
    overstates realised revenue.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["hardware"] = "hardware"

    device_asp: Money
    units_month_1: Sourced[float]
    unit_growth_monthly_pct: Sourced[float]

    consumable_price: Money
    consumables_per_device_per_year: Sourced[float]
    installed_base_start: Sourced[float]

    direct_sales_mix_pct: Sourced[float] = Field(
        description="Share sold direct. The remainder carries the distributor margin."
    )
    distributor_margin_pct: Sourced[float]


RevenueAssumptions = SaaSRevenue | LifeSciencesRevenue | HardwareRevenue


# --------------------------------------------------------------------------- #
# cost structure
# --------------------------------------------------------------------------- #


class COGSAssumptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    # SaaS-shaped
    hosting_pct_of_revenue: Sourced[float]
    hosting_scale_exponent: Sourced[float] = Field(
        description=(
            "Hosting grows with revenue^exponent. Below 1.0 this is what produces "
            "margin expansion, rather than the model simply asserting it."
        )
    )
    support_pct_of_revenue: Sourced[float]
    payment_processing_pct: Sourced[float]

    # Hardware-shaped
    bom_pct_of_asp: Sourced[float]
    consumable_cogs_pct: Sourced[float]
    warranty_pct_of_revenue: Sourced[float]

    target_gross_margin_pct: Sourced[float] = Field(
        description="Sanity bound, not a driver. The model reports what it computes."
    )


class HeadcountAssumptions(BaseModel):
    """Headcount is the primary opex driver, so hiring is triggered, never assumed."""

    model_config = ConfigDict(frozen=True)

    starting: dict[Function, Sourced[float]]
    base_salary: dict[Function, Money]
    loaded_multiplier: Sourced[float]
    annual_attrition_pct: Sourced[float]

    # Sales hiring derives from quota coverage against the revenue plan.
    rep_quota_annual: Money
    rep_ramp_months: Sourced[float]
    quota_attainment_pct: Sourced[float]

    # Customer success hiring derives from the account count.
    accounts_per_csm: Sourced[float]

    # Engineering hiring derives from the product roadmap.
    engineers_per_product_line: Sourced[float]
    product_lines: Sourced[float]


class OpexAssumptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    marketing_pct_of_new_revenue: Sourced[float] = Field(
        description="Programme spend tied to new business won, not a flat % of revenue."
    )
    rd_tooling_per_engineer: Money
    rent_per_fte: Money
    software_per_fte: Money
    ga_fixed_annual: Money


class WorkingCapitalAssumptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    dso_days: Sourced[float]
    dpo_days: Sourced[float]
    inventory_days: Sourced[float]
    capex_pct_of_revenue: Sourced[float]
    capex_depreciation_years: Sourced[float]


class FinancingAssumptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    cash_on_hand: Money
    raise_amount: Money
    raise_month_offset: Sourced[float]
    monthly_burn_at_start: Money


class TaxAssumptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    blended_rate_pct: Sourced[float]
    nol_carryforward_start: Money


# --------------------------------------------------------------------------- #
# the whole thing
# --------------------------------------------------------------------------- #


class ModelAssumptions(BaseModel):
    """Everything `model/` needs, and nothing it has to guess."""

    model_config = ConfigDict(frozen=True)

    company: CompanyMeta
    revenue: RevenueAssumptions
    cogs: COGSAssumptions
    headcount: HeadcountAssumptions
    opex: OpexAssumptions
    working_capital: WorkingCapitalAssumptions
    financing: FinancingAssumptions
    tax: TaxAssumptions

    coverage: CoverageReport
    grounding_warning_count: int = 0

    def by_provenance(self) -> dict[Provenance, int]:
        """How many leaf values came from each source. Drives the footer legend."""
        counts: dict[Provenance, int] = {"deck": 0, "derived": 0, "benchmark": 0, "user": 0}
        for value in _walk(self):
            counts[value.source] += 1
        return counts


def _walk(node: object) -> list[Sourced[float] | Sourced[str]]:
    """Every `Sourced` leaf anywhere in the tree."""
    found: list[Sourced[float] | Sourced[str]] = []
    if isinstance(node, Sourced):
        return [node]
    if isinstance(node, BaseModel):
        for name in type(node).model_fields:
            found.extend(_walk(getattr(node, name)))
    elif isinstance(node, dict):
        for item in node.values():
            found.extend(_walk(item))
    elif isinstance(node, list | tuple):
        for item in node:
            found.extend(_walk(item))
    return found


LifeSciencesRevenue.model_rebuild()
