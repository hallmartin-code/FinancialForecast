"""Benchmark packs. The only place in this codebase allowed to invent a number.

Every value carries where it came from and, just as importantly, *what kind* of
source that is:

- ``published``  -- a specific figure from a named, datable published source.
- ``convention`` -- a planning midpoint in common use across venture finance, with
  no single authoritative publisher. Real, defensible, and not a citation.

That distinction is not decoration. A model built mostly on ``convention`` values is
a template with a company's name on it, and the one-pager says so rather than letting
the reader assume otherwise.

**These are starting values, not house truth.** They are deliberately conservative and
deliberately visible: run ``init-assumptions`` to dump them, replace them with your
own numbers, and pass the file back with ``--assumptions``. User values outrank
benchmarks everywhere.

Nothing in ``model/`` may hardcode a business assumption. If the modelling engine
needs a number that is not in the deck, it comes from here or it does not exist.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from pitchdeck_cfo.models import BusinessModelKind, Sourced

Basis = Literal["published", "convention"]


class BenchmarkValue(BaseModel):
    """A number this tool is willing to supply when the deck does not."""

    model_config = ConfigDict(frozen=True)

    value: float
    unit: str
    source_name: str
    as_of: str
    basis: Basis = "convention"
    url: str | None = None
    note: str | None = None

    @property
    def citation(self) -> str:
        """What appears next to this value in both outputs."""
        marker = "" if self.basis == "published" else ", planning convention"
        return f"{self.source_name} ({self.as_of}{marker})"

    def to_sourced(self) -> Sourced[float]:
        return Sourced[float](
            value=self.value,
            source="benchmark",
            citation=self.citation,
            confidence="medium" if self.basis == "published" else "low",
            note=self.note,
        )


def _convention(
    value: float, unit: str, source: str, note: str | None = None, as_of: str = "2026"
) -> BenchmarkValue:
    return BenchmarkValue(
        value=value, unit=unit, source_name=source, as_of=as_of, basis="convention", note=note
    )


# --------------------------------------------------------------------------- #
# Cost of an employee -- shared by every business model
# --------------------------------------------------------------------------- #

_COMP_SOURCE = (
    "US startup compensation planning midpoint; cross-checked against the ranges "
    "reported by Radford, Pave and Levels.fyi"
)

# Base salary only. The loaded multiplier below adds everything else.
BASE_SALARY_USD: dict[str, BenchmarkValue] = {
    "Engineering": _convention(165_000, "USD/yr", _COMP_SOURCE),
    "Product": _convention(155_000, "USD/yr", _COMP_SOURCE),
    "Design": _convention(140_000, "USD/yr", _COMP_SOURCE),
    "Sales": _convention(
        130_000, "USD/yr", _COMP_SOURCE, note="Base only; OTE is roughly 2x for a quota rep."
    ),
    "Marketing": _convention(130_000, "USD/yr", _COMP_SOURCE),
    "Customer Success": _convention(95_000, "USD/yr", _COMP_SOURCE),
    "G&A": _convention(120_000, "USD/yr", _COMP_SOURCE),
    "Clinical": _convention(150_000, "USD/yr", _COMP_SOURCE),
    "Regulatory": _convention(155_000, "USD/yr", _COMP_SOURCE),
    "Quality": _convention(130_000, "USD/yr", _COMP_SOURCE),
    "Manufacturing": _convention(110_000, "USD/yr", _COMP_SOURCE),
    "Research": _convention(140_000, "USD/yr", _COMP_SOURCE),
}

LOADED_COST_MULTIPLIER = _convention(
    1.28,
    "x base salary",
    "Standard fully-loaded cost planning range of 1.25-1.40x",
    note="Covers employer payroll tax, benefits, equipment and software seats.",
)

# --------------------------------------------------------------------------- #
# Working capital, overhead and tax -- shared
# --------------------------------------------------------------------------- #

COMMON: dict[str, BenchmarkValue] = {
    "dso_days": _convention(
        45, "days", "B2B net-30 terms with typical slippage", note="Net-30 invoiced, paid ~45."
    ),
    "dpo_days": _convention(30, "days", "Standard supplier net-30 terms"),
    "rent_per_fte_usd": _convention(
        6_000, "USD/FTE/yr", "Hybrid-office planning midpoint for US startups"
    ),
    "software_per_fte_usd": _convention(
        3_600, "USD/FTE/yr", "Per-seat SaaS tooling planning midpoint"
    ),
    "ga_fixed_annual_usd": _convention(
        180_000,
        "USD/yr",
        "Legal, accounting, audit, insurance and D&O for a funded early-stage company",
    ),
    "blended_tax_rate_pct": BenchmarkValue(
        value=25.0,
        unit="%",
        source_name="US federal corporate rate of 21% plus a typical state overlay",
        as_of="2026",
        basis="published",
        url="https://www.irs.gov/pub/irs-pdf/i1120.pdf",
        note="Applied only after net operating losses are exhausted.",
    ),
    "capex_depreciation_years": _convention(
        3, "years", "Standard useful life for IT and equipment"
    ),
    "payment_processing_pct": _convention(
        2.9, "%", "Card-processing rate for a standard merchant account"
    ),
    "capex_pct_of_revenue": _convention(
        2, "%", "Equipment and IT capex for an asset-light company"
    ),
    "annual_attrition_pct": _convention(
        15, "%", "Voluntary attrition planning rate for a growing startup"
    ),
    "product_lines": _convention(1, "count", "One product line unless the deck names more"),
    "inventory_days": _convention(
        0, "days", "No inventory for a company that does not ship a physical good"
    ),
}

# --------------------------------------------------------------------------- #
# SaaS
# --------------------------------------------------------------------------- #

_SAAS_SURVEY = (
    "Range reported across the annual B2B SaaS retention and metrics surveys "
    "(SaaS Capital, KeyBanc/Pacific Crest)"
)

SAAS: dict[str, BenchmarkValue] = {
    "gross_margin_pct": _convention(
        78, "%", "Reported B2B SaaS gross margin range of 70-80% at scale"
    ),
    "hosting_pct_of_revenue": _convention(
        8, "%", "Infrastructure share of revenue at scale", note="Higher early, falls with scale."
    ),
    "hosting_scale_exponent": _convention(
        0.85,
        "exponent",
        "Sub-linear infrastructure scaling",
        note="Hosting cost grows with revenue^0.85, not 1:1 -- this is what produces "
        "margin expansion, rather than asserting the expansion directly.",
    ),
    "support_pct_of_revenue": _convention(4, "%", "Support and success cost inside COGS"),
    "logo_churn_annual_pct": _convention(12, "%", _SAAS_SURVEY, note="Mid-market default."),
    "net_revenue_retention_pct": _convention(110, "%", _SAAS_SURVEY, note="Mid-market default."),
    "annual_prepay_mix_pct": _convention(
        50, "%", "Share of contracts billed annually up front", note="Drives deferred revenue."
    ),
    "rep_quota_annual_usd": _convention(600_000, "USD/yr", "Mid-market AE quota planning midpoint"),
    "rep_ramp_months": _convention(6, "months", "Time to full productivity for a new AE"),
    "quota_attainment_pct": _convention(
        75, "%", "Planning attainment against quota", note="Modelling at 100% overstates revenue."
    ),
    "accounts_per_csm": _convention(40, "accounts", "Mid-market CSM book-of-business midpoint"),
    "marketing_pct_of_new_arr": _convention(
        40, "%", "Programme spend as a share of new ARR", note="Excludes marketing headcount."
    ),
    "engineers_per_product_line": _convention(6, "FTE", "Team size to sustain one product line"),
    "arpu_annual_usd": _convention(
        24_000,
        "USD/yr",
        "Mid-market B2B SaaS annual contract value midpoint",
        note="Only used when the deck gives neither ACV/ARPU nor an ARR-and-customer "
        "pair to derive it from. A model resting on this is a template.",
    ),
    "new_logo_growth_monthly_pct": _convention(
        5, "%", "Monthly new-logo growth for an early-stage company finding its motion"
    ),
    "new_logos_month_1": _convention(
        3, "logos/mo", "Starting new-logo rate before a sales team is hired against quota"
    ),
}

# --------------------------------------------------------------------------- #
# Life sciences -- pre-revenue and milestone-driven
# --------------------------------------------------------------------------- #

LIFE_SCIENCES: dict[str, BenchmarkValue] = {
    "ind_enabling_cost_usd": _convention(
        2_500_000, "USD", "IND-enabling toxicology and CMC package for a small molecule"
    ),
    "ind_enabling_months": _convention(15, "months", "IND-enabling programme duration"),
    "phase_1_cost_usd": _convention(4_000_000, "USD", "First-in-human safety study"),
    "phase_1_months": _convention(18, "months", "Phase 1 duration including readout"),
    "phase_2_cost_usd": _convention(15_000_000, "USD", "Proof-of-concept efficacy study"),
    "phase_2_months": _convention(24, "months", "Phase 2 duration including readout"),
    "rd_headcount_preclinical": _convention(6, "FTE", "Core team through IND-enabling work"),
    "ga_pct_of_rd": _convention(25, "%", "G&A as a share of R&D for a clinical-stage company"),
    "royalty_rate_pct": _convention(
        8, "%", "Royalty range of 5-12% on net sales in early-stage licensing deals"
    ),
    "capitalised_rd_cost_per_approval_usd": BenchmarkValue(
        value=2_558_000_000,
        unit="USD",
        source_name="DiMasi, Grabowski & Hansen, Journal of Health Economics 47 (2016)",
        as_of="2016",
        basis="published",
        url="https://doi.org/10.1016/j.jhealeco.2016.01.012",
        note="Capitalised cost per approved drug, including failures. Context for "
        "valuation discussion only -- not a line in the operating model.",
    ),
}

# --------------------------------------------------------------------------- #
# Hardware / device
# --------------------------------------------------------------------------- #

HARDWARE: dict[str, BenchmarkValue] = {
    "device_gross_margin_pct": _convention(
        60, "%", "Medical device gross margin range of 55-70% for a durable unit"
    ),
    "consumable_gross_margin_pct": _convention(
        75, "%", "Disposable consumable margin, typically above the device it serves"
    ),
    "bom_pct_of_asp": _convention(
        40, "%", "Bill of materials as a share of ASP", note="The complement of device margin."
    ),
    "distributor_margin_pct": _convention(
        25, "%", "Distributor share of list price where sold indirectly"
    ),
    "consumables_per_device_per_year": _convention(
        24, "units", "Attach rate for a recurring disposable"
    ),
    "units_per_rep_per_year": _convention(120, "units", "Capital-equipment rep productivity"),
    "inventory_days": _convention(
        75, "days", "Finished-goods and component cover for a physical product"
    ),
    "warranty_pct_of_revenue": _convention(2, "%", "Warranty and field service reserve"),
    "device_asp_usd": _convention(
        4_000, "USD", "Capital-equipment ASP placeholder", note="Replace with the deck's own price."
    ),
    "consumable_price_usd": _convention(30, "USD", "Disposable consumable price placeholder"),
    "units_month_1": _convention(5, "units/mo", "Initial placement rate before a sales team ramps"),
    "unit_growth_monthly_pct": _convention(
        6, "%", "Monthly placement growth during commercial ramp"
    ),
    "direct_sales_mix_pct": _convention(
        50, "%", "Split between direct and distributor channels at launch"
    ),
    "fda_510k_review_months": BenchmarkValue(
        value=5.0,
        unit="months",
        source_name="FDA MDUFA performance goal for 510(k) decisions",
        as_of="2026",
        basis="published",
        url="https://www.fda.gov/medical-devices/premarket-notification-510k",
        note="Review clock only. Preparation and any additional-information cycle "
        "sit outside it, so plan longer.",
    ),
}


PACKS: dict[BusinessModelKind, dict[str, BenchmarkValue]] = {
    "saas": SAAS,
    "life_sciences": LIFE_SCIENCES,
    "hardware": HARDWARE,
}


def pack_for(business_model: BusinessModelKind) -> dict[str, BenchmarkValue]:
    """Benchmarks for one business model, merged over the shared ones."""
    return {**COMMON, **PACKS.get(business_model, {})}


def salary_for(function: str) -> BenchmarkValue:
    """Base salary for a function, falling back to G&A for anything unrecognised."""
    return BASE_SALARY_USD.get(function, BASE_SALARY_USD["G&A"])


def published_count(pack: dict[str, BenchmarkValue]) -> tuple[int, int]:
    """How many values in a pack rest on a published figure rather than a convention."""
    published = sum(1 for value in pack.values() if value.basis == "published")
    return published, len(pack)
