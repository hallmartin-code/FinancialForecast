"""Resolve `DeckFacts` + benchmarks + user overrides into `ModelAssumptions`.

This is the only stage permitted to fill a gap, and every gap it fills is labelled.
The `Resolver` below is the single choke point: nothing becomes an assumption without
passing through `Resolver.pick`, which records the provenance it settled on. Coverage
is therefore a fact about what happened, not a separate tally that could drift.

Precedence, highest first: **user > deck > derived > benchmark.**
"""

from __future__ import annotations

import re
from datetime import date
from typing import TYPE_CHECKING, Any

from pitchdeck_cfo.assume import benchmarks as bm
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
    RevenueAssumptions,
    SaaSRevenue,
    TaxAssumptions,
    WorkingCapitalAssumptions,
)
from pitchdeck_cfo.errors import UnsupportedBusinessModelError
from pitchdeck_cfo.extract.schema import DeckFacts, FinancialMetric
from pitchdeck_cfo.models import (
    SUPPORTED_BUSINESS_MODELS,
    BusinessModelKind,
    CoverageReport,
    Provenance,
    Sourced,
)

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Mapping

# The inputs the coverage ratio is measured over. Not every assumption -- the ones
# that actually move the model. A run can carry a hundred benchmark values for
# rent-per-desk and still be a good model; it cannot if it invented the revenue base.
CORE_INPUTS: dict[BusinessModelKind, tuple[str, ...]] = {
    "saas": (
        "starting_arr",
        "starting_customers",
        "arpu_annual",
        "new_logos_month_1",
        "new_logo_growth_monthly_pct",
        "logo_churn_annual_pct",
        "net_revenue_retention_pct",
        "gross_margin_pct",
        "cash_on_hand",
        "raise_amount",
        "monthly_burn_at_start",
        "starting_headcount",
    ),
    "life_sciences": (
        "cash_on_hand",
        "raise_amount",
        "monthly_burn_at_start",
        "starting_headcount",
        "non_dilutive_funding",
        "phase_1_cost",
        "phase_1_duration",
        "next_milestone_cost",
    ),
    "hardware": (
        "device_asp",
        "consumable_price",
        "units_month_1",
        "unit_growth_monthly_pct",
        "consumables_per_device_per_year",
        "gross_margin_pct",
        "cash_on_hand",
        "raise_amount",
        "monthly_burn_at_start",
        "starting_headcount",
    ),
}


class Resolver:
    """Picks a value for a named input and remembers where it came from."""

    def __init__(
        self,
        pack: Mapping[str, bm.BenchmarkValue],
        overrides: Mapping[str, Any],
    ) -> None:
        self.pack = pack
        self.overrides = overrides
        self.chosen: dict[str, Provenance] = {}

    def pick(
        self,
        name: str,
        *,
        deck: Sourced[float] | None = None,
        derived: tuple[float, str] | None = None,
        benchmark: str | None = None,
        fallback: tuple[float, str] | None = None,
    ) -> Sourced[float]:
        """Resolve one input in strict precedence order.

        `derived` and `fallback` are both arithmetic, and differ only in confidence:
        a derivation computes the value from other real figures, while a fallback is
        the structural answer when there is nothing to compute from (a pre-revenue
        company genuinely has zero ARR, and saying so is not a guess).
        """
        if name in self.overrides:
            self.chosen[name] = "user"
            return Sourced[float](
                value=float(self.overrides[name]),
                source="user",
                citation="assumptions.yaml",
                confidence="high",
            )

        if deck is not None:
            self.chosen[name] = "deck"
            return deck

        if derived is not None:
            value, note = derived
            self.chosen[name] = "derived"
            return Sourced[float](value=value, source="derived", confidence="medium", note=note)

        if benchmark is not None and benchmark in self.pack:
            self.chosen[name] = "benchmark"
            return self.pack[benchmark].to_sourced()

        if fallback is not None:
            value, note = fallback
            self.chosen[name] = "derived"
            return Sourced[float](value=value, source="derived", confidence="high", note=note)

        raise KeyError(f"no source available for assumption {name!r}")

    def coverage(self, core_inputs: tuple[str, ...]) -> CoverageReport:
        by_source: dict[Provenance, list[str]] = {
            "deck": [],
            "derived": [],
            "benchmark": [],
            "user": [],
        }
        for name in core_inputs:
            source = self.chosen.get(name)
            if source is not None:
                by_source[source].append(name)
        return CoverageReport(
            core_inputs=core_inputs,
            by_source={key: tuple(values) for key, values in by_source.items()},
        )


# --------------------------------------------------------------------------- #
# helpers over DeckFacts
# --------------------------------------------------------------------------- #


def _metric(facts: DeckFacts, name: FinancialMetric) -> Sourced[float] | None:
    """A deck-stated metric as a `Sourced`, or None when the deck does not give it."""
    fact = facts.financials.get(name)
    if fact is None:
        return None
    return Sourced[float](
        value=fact.value,
        source="deck",
        citation=fact.citation,
        confidence=fact.confidence,
        note=None,
    )


def _cited_money(node: Any) -> Sourced[float] | None:
    if node is None:
        return None
    return Sourced[float](
        value=float(node.value),
        source="deck",
        citation=node.citation,
        confidence=node.confidence,
        note=None,
    )


def _start_month(facts: DeckFacts, today: date) -> tuple[str, Sourced[str]]:
    """Year 1 begins the month after the deck's stated 'as of' date, else after today.

    Stated rather than assumed silently: the basis appears on both artifacts.
    """
    stated = facts.financials.as_of_date
    anchor = today
    if stated is not None:
        parsed = _parse_month(stated.value)
        if parsed is not None:
            anchor = parsed
            basis = Sourced[str](
                value=f"deck states figures as of {stated.value}",
                source="deck",
                citation=stated.citation,
                confidence=stated.confidence,
            )
            return _next_month(anchor), basis

    basis = Sourced[str](
        value=f"deck states no 'as of' date; calendar anchored to the run date {today:%Y-%m-%d}",
        source="derived",
        confidence="high",
    )
    return _next_month(anchor), basis


def _parse_month(text: str) -> date | None:
    """Pull a year, and a month when one is written, out of free-form deck text."""
    year = re.search(r"(20\d{2})", text)
    if year is None:
        return None
    months = [
        "jan",
        "feb",
        "mar",
        "apr",
        "may",
        "jun",
        "jul",
        "aug",
        "sep",
        "oct",
        "nov",
        "dec",
    ]
    lowered = text.lower()
    month = next((i + 1 for i, name in enumerate(months) if name in lowered), None)
    if month is None:
        quarter = re.search(r"q([1-4])", lowered)
        month = int(quarter.group(1)) * 3 if quarter else 12
    return date(int(year.group(1)), month, 1)


def _next_month(anchor: date) -> str:
    year, month = anchor.year, anchor.month + 1
    if month > 12:
        year, month = year + 1, 1
    return f"{year:04d}-{month:02d}"


def _annual_to_monthly_growth(annual_pct: float) -> float:
    """A 118% annual growth rate is not 9.8% a month. Compound it properly."""
    return float(((1.0 + annual_pct / 100.0) ** (1.0 / 12.0) - 1.0) * 100.0)


# --------------------------------------------------------------------------- #
# revenue engines
# --------------------------------------------------------------------------- #


def _saas_revenue(facts: DeckFacts, r: Resolver) -> SaaSRevenue:
    arr = _metric(facts, "current_arr_usd")
    mrr = _metric(facts, "current_mrr_usd")
    customers = _metric(facts, "customer_count")
    acv = _metric(facts, "acv_usd") or _metric(facts, "arpu_usd")
    growth = _metric(facts, "growth_rate_pct")

    starting_arr = r.pick(
        "starting_arr",
        deck=arr,
        derived=(mrr.value * 12, "12 x stated MRR") if mrr else None,
        fallback=(0.0, "deck states no revenue; treated as pre-revenue"),
    )

    arpu = r.pick(
        "arpu_annual",
        deck=acv,
        derived=(
            (starting_arr.value / customers.value, "stated ARR divided by stated customer count")
            if customers and customers.value > 0 and starting_arr.value > 0
            else None
        ),
        benchmark="arpu_annual_usd",
    )

    starting_customers = r.pick(
        "starting_customers",
        deck=customers,
        derived=(
            (starting_arr.value / arpu.value, "starting ARR divided by ARPU")
            if arpu.value > 0 and starting_arr.value > 0
            else None
        ),
        fallback=(0.0, "deck states no customers"),
    )

    monthly_growth = r.pick(
        "new_logo_growth_monthly_pct",
        derived=(
            (_annual_to_monthly_growth(growth.value), f"{growth.value:g}% annual growth compounded")
            if growth
            else None
        ),
        benchmark="new_logo_growth_monthly_pct",
    )

    return SaaSRevenue(
        starting_arr=starting_arr,
        starting_customers=starting_customers,
        arpu_annual=arpu,
        new_logos_month_1=r.pick(
            "new_logos_month_1",
            derived=(
                (
                    starting_customers.value * monthly_growth.value / 100.0,
                    "current customer base times the monthly growth rate",
                )
                if starting_customers.value > 0
                else None
            ),
            benchmark="new_logos_month_1",
        ),
        new_logo_growth_monthly_pct=monthly_growth,
        growth_decay_annual_pct=r.pick(
            "growth_decay_annual_pct", benchmark="growth_decay_annual_pct"
        ),
        terminal_growth_monthly_pct=r.pick(
            "terminal_growth_monthly_pct", benchmark="terminal_growth_monthly_pct"
        ),
        logo_churn_annual_pct=r.pick(
            "logo_churn_annual_pct",
            deck=_metric(facts, "logo_churn_pct"),
            benchmark="logo_churn_annual_pct",
        ),
        net_revenue_retention_pct=r.pick(
            "net_revenue_retention_pct",
            deck=_metric(facts, "net_revenue_retention_pct"),
            benchmark="net_revenue_retention_pct",
        ),
        annual_prepay_mix_pct=r.pick("annual_prepay_mix_pct", benchmark="annual_prepay_mix_pct"),
    )


def _life_sciences_revenue(facts: DeckFacts, r: Resolver) -> LifeSciencesRevenue:
    ls = facts.financials.life_sciences
    milestone = ls.next_milestone

    next_cost = r.pick(
        "next_milestone_cost",
        deck=_cited_money(milestone.cost_usd) if milestone else None,
        benchmark="ind_enabling_cost_usd",
    )
    phase_1_cost = r.pick("phase_1_cost", benchmark="phase_1_cost_usd")
    phase_1_months = r.pick("phase_1_duration", benchmark="phase_1_months")
    ind_months = r.pick("ind_enabling_duration", benchmark="ind_enabling_months")

    phases = (
        ProgramPhase(
            name=(
                milestone.description.value[:60]
                if milestone is not None
                else "IND-enabling programme"
            ),
            start_month_offset=Sourced[float](
                value=0.0, source="derived", confidence="high", note="Starts at the raise."
            ),
            duration_months=ind_months,
            total_cost=next_cost,
        ),
        ProgramPhase(
            name="Phase 1",
            start_month_offset=Sourced[float](
                value=ind_months.value,
                source="derived",
                confidence="medium",
                note="Follows the IND-enabling programme.",
            ),
            duration_months=phase_1_months,
            total_cost=phase_1_cost,
        ),
    )

    return LifeSciencesRevenue(
        program_phases=phases,
        non_dilutive_funding=r.pick(
            "non_dilutive_funding",
            deck=_cited_money(ls.non_dilutive_funding_usd),
            fallback=(0.0, "deck states no grant or non-dilutive funding"),
        ),
        partnership_upfront=r.pick(
            "partnership_upfront",
            fallback=(0.0, "no signed partnership stated; modelled as zero rather than assumed"),
        ),
        royalty_rate_pct=r.pick("royalty_rate_pct", benchmark="royalty_rate_pct"),
    )


def _hardware_revenue(facts: DeckFacts, r: Resolver) -> HardwareRevenue:
    growth = _metric(facts, "growth_rate_pct")
    return HardwareRevenue(
        device_asp=r.pick(
            "device_asp", deck=_metric(facts, "device_asp_usd"), benchmark="device_asp_usd"
        ),
        units_month_1=r.pick("units_month_1", benchmark="units_month_1"),
        unit_growth_monthly_pct=r.pick(
            "unit_growth_monthly_pct",
            derived=(
                (
                    _annual_to_monthly_growth(growth.value),
                    f"{growth.value:g}% annual growth compounded",
                )
                if growth
                else None
            ),
            benchmark="unit_growth_monthly_pct",
        ),
        growth_decay_annual_pct=r.pick(
            "growth_decay_annual_pct", benchmark="growth_decay_annual_pct"
        ),
        terminal_growth_monthly_pct=r.pick(
            "terminal_growth_monthly_pct", benchmark="terminal_growth_monthly_pct"
        ),
        consumable_price=r.pick(
            "consumable_price",
            deck=_metric(facts, "consumable_price_usd"),
            benchmark="consumable_price_usd",
        ),
        consumables_per_device_per_year=r.pick(
            "consumables_per_device_per_year", benchmark="consumables_per_device_per_year"
        ),
        installed_base_start=r.pick(
            "installed_base_start",
            deck=_metric(facts, "installed_base_units"),
            fallback=(0.0, "deck states no installed base"),
        ),
        direct_sales_mix_pct=r.pick("direct_sales_mix_pct", benchmark="direct_sales_mix_pct"),
        distributor_margin_pct=r.pick("distributor_margin_pct", benchmark="distributor_margin_pct"),
    )


# --------------------------------------------------------------------------- #
# the resolver entry point
# --------------------------------------------------------------------------- #


def resolve(
    facts: DeckFacts,
    *,
    horizon_years: int = 5,
    loaded_multiplier: float | None = None,
    overrides: Mapping[str, Any] | None = None,
    today: date | None = None,
) -> ModelAssumptions:
    """Turn what the deck states into a complete, labelled set of model inputs."""
    overrides = overrides or {}
    today = today or date.today()

    profile = facts.profile
    business_model: BusinessModelKind = (
        profile.business_model.value if profile.business_model is not None else "unknown"
    )
    if "business_model" in overrides:
        business_model = overrides["business_model"]
    if business_model not in SUPPORTED_BUSINESS_MODELS:
        raise UnsupportedBusinessModelError(business_model, SUPPORTED_BUSINESS_MODELS)

    pack = bm.pack_for(business_model)
    r = Resolver(pack, overrides)

    start_month, as_of_basis = _start_month(facts, today)

    revenue: RevenueAssumptions
    if business_model == "saas":
        revenue = _saas_revenue(facts, r)
    elif business_model == "life_sciences":
        revenue = _life_sciences_revenue(facts, r)
    else:
        revenue = _hardware_revenue(facts, r)

    headcount_start = {
        entry.function: float(entry.count.value) for entry in facts.financials.headcount_by_function
    }
    team_size = _cited_money(profile.team_size)
    starting_headcount = r.pick(
        "starting_headcount",
        deck=(
            Sourced[float](
                value=sum(headcount_start.values()),
                source="deck",
                citation=facts.financials.headcount_by_function[0].count.citation,
                confidence="high",
            )
            if headcount_start
            else team_size
        ),
        benchmark="rd_headcount_preclinical" if business_model == "life_sciences" else None,
        fallback=(3.0, "deck states no headcount; assumed a three-person founding team"),
    )

    assumptions = ModelAssumptions(
        company=CompanyMeta(
            name=profile.company_name.value if profile.company_name else "Unnamed company",
            business_model=business_model,
            sector=profile.sector.value if profile.sector else None,
            currency=facts.financials.currency.value if facts.financials.currency else "USD",
            start_month=start_month,
            horizon_years=horizon_years,
            as_of_basis=as_of_basis,
        ),
        revenue=revenue,
        cogs=_cogs(facts, r, business_model),
        headcount=_headcount(
            r, headcount_start, starting_headcount, loaded_multiplier, business_model
        ),
        opex=_opex(r, business_model),
        working_capital=_working_capital(r),
        financing=_financing(facts, r),
        tax=_tax(r),
        coverage=r.coverage(CORE_INPUTS[business_model]),
        grounding_warning_count=len(facts.grounding_warnings),
    )
    return assumptions


def _cogs(facts: DeckFacts, r: Resolver, business_model: BusinessModelKind) -> COGSAssumptions:
    margin_benchmark = {
        "saas": "gross_margin_pct",
        "hardware": "device_gross_margin_pct",
    }.get(business_model)
    return COGSAssumptions(
        hosting_pct_of_revenue=r.pick(
            "hosting_pct_of_revenue", benchmark="hosting_pct_of_revenue", fallback=(0.0, "n/a")
        ),
        hosting_scale_exponent=r.pick(
            "hosting_scale_exponent",
            benchmark="hosting_scale_exponent",
            fallback=(1.0, "no scale curve for this business model"),
        ),
        support_pct_of_revenue=r.pick(
            "support_pct_of_revenue", benchmark="support_pct_of_revenue", fallback=(0.0, "n/a")
        ),
        payment_processing_pct=r.pick("payment_processing_pct", benchmark="payment_processing_pct"),
        bom_pct_of_asp=r.pick(
            "bom_pct_of_asp", benchmark="bom_pct_of_asp", fallback=(0.0, "no physical product")
        ),
        consumable_gross_margin_pct=r.pick(
            "consumable_gross_margin_pct",
            benchmark="consumable_gross_margin_pct",
            fallback=(0.0, "no consumable stream"),
        ),
        warranty_pct_of_revenue=r.pick(
            "warranty_pct_of_revenue",
            benchmark="warranty_pct_of_revenue",
            fallback=(0.0, "no physical product"),
        ),
        target_gross_margin_pct=r.pick(
            "gross_margin_pct",
            deck=_metric(facts, "gross_margin_pct"),
            benchmark=margin_benchmark,
            fallback=(0.0, "pre-revenue; no gross margin to state"),
        ),
    )


def _headcount(
    r: Resolver,
    stated: dict[str, float],
    starting_total: Sourced[float],
    loaded_multiplier: float | None,
    business_model: BusinessModelKind = "saas",
) -> HeadcountAssumptions:
    functions: tuple[Function, ...] = (
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
    )
    starting: dict[Function, Sourced[float]] = {}
    for function in functions:
        if function in stated:
            starting[function] = Sourced[float](
                value=stated[function], source="deck", citation="deck headcount", confidence="high"
            )
        else:
            starting[function] = Sourced[float](
                value=0.0,
                source="derived",
                confidence="medium",
                note="deck names no one in this function",
            )
    if not stated:
        # The deck gave a headcount total with no functional split. Placing it in the
        # function the business actually runs on beats inventing a split -- and beats
        # the previous behaviour of parking a preclinical team in G&A, which made a
        # research company look like an admin one.
        core_by_model: dict[BusinessModelKind, Function] = {
            "life_sciences": "Research",
            "saas": "Engineering",
            "hardware": "Engineering",
        }
        core: Function = core_by_model.get(business_model, "G&A")
        starting[core] = starting_total

    return HeadcountAssumptions(
        starting=starting,
        base_salary={function: bm.salary_for(function).to_sourced() for function in functions},
        loaded_multiplier=(
            Sourced[float](
                value=loaded_multiplier,
                source="user",
                citation="--loaded-multiplier",
                confidence="high",
            )
            if loaded_multiplier is not None
            else bm.LOADED_COST_MULTIPLIER.to_sourced()
        ),
        annual_attrition_pct=r.pick("annual_attrition_pct", benchmark="annual_attrition_pct"),
        rep_quota_annual=r.pick(
            "rep_quota_annual", benchmark="rep_quota_annual_usd", fallback=(0.0, "no quota model")
        ),
        rep_ramp_months=r.pick(
            "rep_ramp_months", benchmark="rep_ramp_months", fallback=(6.0, "standard ramp")
        ),
        quota_attainment_pct=r.pick(
            "quota_attainment_pct", benchmark="quota_attainment_pct", fallback=(75.0, "standard")
        ),
        accounts_per_csm=r.pick(
            "accounts_per_csm", benchmark="accounts_per_csm", fallback=(0.0, "no CSM model")
        ),
        engineers_per_product_line=r.pick(
            "engineers_per_product_line",
            benchmark="engineers_per_product_line",
            fallback=(6.0, "standard team size"),
        ),
        product_lines=r.pick("product_lines", benchmark="product_lines"),
        revenue_per_engineer=r.pick("revenue_per_engineer", benchmark="revenue_per_engineer_usd"),
        sales_per_marketing_hire=r.pick(
            "sales_per_marketing_hire", benchmark="sales_per_marketing_hire"
        ),
        ftes_per_ga_hire=r.pick("ftes_per_ga_hire", benchmark="ftes_per_ga_hire"),
        scale_exponent=r.pick("headcount_scale_exponent", benchmark="headcount_scale_exponent"),
    )


def _opex(r: Resolver, business_model: BusinessModelKind) -> OpexAssumptions:
    return OpexAssumptions(
        marketing_pct_of_new_revenue=r.pick(
            "marketing_pct_of_new_revenue",
            benchmark="marketing_pct_of_new_arr",
            fallback=(15.0, "programme spend for a company without a logo-based motion"),
        ),
        rd_tooling_per_engineer=r.pick(
            "rd_tooling_per_engineer",
            fallback=(6_000.0, "cloud dev environments and tooling per engineer"),
        ),
        rent_per_fte=r.pick("rent_per_fte", benchmark="rent_per_fte_usd"),
        software_per_fte=r.pick("software_per_fte", benchmark="software_per_fte_usd"),
        ga_fixed_annual=r.pick("ga_fixed_annual", benchmark="ga_fixed_annual_usd"),
    )


def _working_capital(r: Resolver) -> WorkingCapitalAssumptions:
    return WorkingCapitalAssumptions(
        dso_days=r.pick("dso_days", benchmark="dso_days"),
        dpo_days=r.pick("dpo_days", benchmark="dpo_days"),
        inventory_days=r.pick("inventory_days", benchmark="inventory_days"),
        capex_pct_of_revenue=r.pick("capex_pct_of_revenue", benchmark="capex_pct_of_revenue"),
        capex_depreciation_years=r.pick(
            "capex_depreciation_years", benchmark="capex_depreciation_years"
        ),
    )


def _financing(facts: DeckFacts, r: Resolver) -> FinancingAssumptions:
    raise_amount = r.pick(
        "raise_amount",
        deck=_cited_money(facts.profile.raise_amount_usd),
        fallback=(0.0, "deck states no raise"),
    )
    runway = _metric(facts, "runway_months")
    cash = _metric(facts, "cash_on_hand_usd")

    # A deck that states a raise and the runway it buys has stated its burn rate,
    # just not in those words. Deriving it beats reaching for a benchmark.
    derived_burn: tuple[float, str] | None = None
    if runway is not None and runway.value > 0:
        pool = (cash.value if cash else 0.0) + raise_amount.value
        if pool > 0:
            derived_burn = (
                pool / runway.value,
                f"cash plus the raise divided by the stated {runway.value:g}-month runway",
            )

    return FinancingAssumptions(
        cash_on_hand=r.pick(
            "cash_on_hand",
            deck=cash,
            fallback=(0.0, "deck states no cash position"),
        ),
        raise_amount=raise_amount,
        raise_month_offset=r.pick(
            "raise_month_offset",
            fallback=(0.0, "raise modelled as closing at the start of the horizon"),
        ),
        monthly_burn_at_start=r.pick(
            "monthly_burn_at_start",
            deck=_metric(facts, "monthly_burn_usd"),
            derived=derived_burn,
            fallback=(0.0, "deck states no burn rate; derived from the opex build instead"),
        ),
    )


def _tax(r: Resolver) -> TaxAssumptions:
    return TaxAssumptions(
        blended_rate_pct=r.pick("blended_tax_rate_pct", benchmark="blended_tax_rate_pct"),
        nol_carryforward_start=r.pick(
            "nol_carryforward_start", fallback=(0.0, "no prior losses stated")
        ),
    )
