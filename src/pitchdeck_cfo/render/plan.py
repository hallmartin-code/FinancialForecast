"""Turning the monthly model into the annual drivers the workbook is built from.

The engine works monthly because break-even, peak cash need and runway are month-level
facts. The workbook is annual because sixty columns is not a document anyone reads.
This module is the bridge, and its one rule is that **the annual drivers must be
genuine annual metrics, not constants back-solved to make a total come out right.**

Two are worth explaining, because they look like fudges and are not:

*Average revenue per customer* rises year on year. That is what net revenue retention
above 100% means — the same accounts pay more — so a driver that stayed flat would be
the wrong number, not the honest one.

*Realised ASP* is device revenue over units, which is below list because part of the
channel goes through a distributor. It is the price the company actually collects.

Because both are real, the annual build reproduces the monthly engine's annual totals
exactly rather than approximately, and the workbook cannot disagree with the one-pager.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from pitchdeck_cfo.assume.schema import HardwareRevenue, LifeSciencesRevenue, SaaSRevenue
from pitchdeck_cfo.model.build import FinancialModel
from pitchdeck_cfo.model.headcount import FUNCTIONS
from pitchdeck_cfo.model.timeline import MONTHS_PER_YEAR, Timeline
from pitchdeck_cfo.models import Provenance

Kind = Literal["money", "count", "percent", "ratio"]


@dataclass(frozen=True)
class Line:
    """One assumption row: a label, a value per year, and where it came from."""

    key: str
    label: str
    values: tuple[float, ...]
    kind: Kind = "money"
    source: Provenance = "derived"
    citation: str | None = None
    note: str | None = None

    @property
    def flagged(self) -> bool:
        """Benchmarks are the company's missing numbers, so they are marked."""
        return self.source == "benchmark"


@dataclass(frozen=True)
class Section:
    title: str
    lines: tuple[Line, ...]


@dataclass(frozen=True)
class Stream:
    """A revenue stream, as a volume driver, a price driver, and their product."""

    name: str
    volume_key: str
    volume_label: str
    price_key: str
    price_label: str
    revenue_label: str


@dataclass(frozen=True)
class AnnualPlan:
    """Everything the workbook needs, already annual."""

    years: tuple[str, ...]
    sections: tuple[Section, ...]
    streams: tuple[Stream, ...]
    functions: tuple[str, ...]
    observations: tuple[str, ...] = field(default_factory=tuple)

    def line(self, key: str) -> Line | None:
        for section in self.sections:
            for line in section.lines:
                if line.key == key:
                    return line
        return None


def _safe_ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    """A ratio that yields zero where the denominator does not permit one."""
    result: np.ndarray = np.divide(
        numerator,
        denominator,
        out=np.zeros_like(numerator, dtype=float),
        where=denominator > 0,
    )
    return result


def _repeat(value: float, years: int) -> tuple[float, ...]:
    """A driver the model holds constant still gets a cell per year, so it can be tapered."""
    return tuple([value] * years)


def build(model: FinancialModel) -> AnnualPlan:
    """Derive the annual driver set from a built model."""
    assumptions = model.assumptions
    timeline = Timeline(
        start_month=assumptions.company.start_month,
        horizon_years=assumptions.company.horizon_years,
    )
    years = tuple(
        str(int(assumptions.company.start_month[:4]) + i)
        for i in range(assumptions.company.horizon_years)
    )
    n = len(years)

    revenue = assumptions.revenue
    if isinstance(revenue, SaaSRevenue):
        revenue_section, streams = _saas(model, timeline, n)
    elif isinstance(revenue, HardwareRevenue):
        revenue_section, streams = _hardware(model, timeline, n)
    else:
        revenue_section, streams = _life_sciences(model, n)

    sections = (
        revenue_section,
        _cogs(model, n),
        _headcount(model, n),
        _non_personnel(model, n),
        _cash(model, n),
    )
    return AnnualPlan(
        years=years,
        sections=sections,
        streams=streams,
        functions=tuple(_active_functions(model)),
        observations=observations(model),
    )


# --------------------------------------------------------------------------- #
# revenue, per engine
# --------------------------------------------------------------------------- #


def _saas(model: FinancialModel, _timeline: Timeline, n: int) -> tuple[Section, tuple[Stream, ...]]:
    assumptions = model.assumptions
    revenue = assumptions.revenue
    assert isinstance(revenue, SaaSRevenue)

    annual_revenue = np.asarray(model.pnl_annual["Revenue"])
    arpu = revenue.arpu_annual.value

    # The real customer base, averaged over each year. Dividing revenue by contract
    # value instead would make revenue-per-customer come out flat by construction,
    # which would contradict the expansion the model actually carries.
    customers = np.asarray(model.customers_annual or [0.0] * n, dtype=float)
    per_customer = np.where(customers > 0, _safe_ratio(annual_revenue, customers), arpu)

    return (
        Section(
            "Revenue assumptions",
            (
                Line(
                    "saas_customers",
                    "Average paying customers",
                    tuple(float(v) for v in customers),
                    "count",
                    revenue.starting_customers.source,
                    revenue.starting_customers.citation,
                    "Average over the year, not the year-end count.",
                ),
                Line(
                    "saas_arpu",
                    "Average revenue per customer ($000)",
                    tuple(float(v) / 1000.0 for v in per_customer),
                    "money",
                    revenue.arpu_annual.source,
                    revenue.arpu_annual.citation,
                    "Rises with expansion: net revenue retention above 100% means the "
                    "same accounts pay more each year.",
                ),
            ),
        ),
        (
            Stream(
                "Subscription revenue",
                "saas_customers",
                "Average paying customers",
                "saas_arpu",
                "Average revenue per customer ($000)",
                "Subscription revenue",
            ),
        ),
    )


def _hardware(
    model: FinancialModel, _timeline: Timeline, n: int
) -> tuple[Section, tuple[Stream, ...]]:
    revenue = model.assumptions.revenue
    assert isinstance(revenue, HardwareRevenue)

    device = np.asarray(model.revenue_components_annual.get("device", [0.0] * n))
    consumables = np.asarray(model.revenue_components_annual.get("consumables", [0.0] * n))

    units = np.asarray(
        [
            device[i] / (revenue.device_asp.value * _realisation(revenue))
            if revenue.device_asp.value > 0
            else 0.0
            for i in range(n)
        ]
    )
    realised_asp = _safe_ratio(device, units)
    realised_asp = np.where(units > 0, realised_asp, revenue.device_asp.value)

    consumable_units = np.asarray(
        [
            consumables[i] / (revenue.consumable_price.value * _realisation(revenue))
            if revenue.consumable_price.value > 0
            else 0.0
            for i in range(n)
        ]
    )
    realised_consumable = _safe_ratio(consumables, consumable_units)
    realised_consumable = np.where(
        consumable_units > 0, realised_consumable, revenue.consumable_price.value
    )

    channel_note = (
        f"Net of the distributor margin on the "
        f"{100 - revenue.direct_sales_mix_pct.value:.0f}% of volume sold indirectly."
    )

    return (
        Section(
            "Revenue assumptions",
            (
                Line(
                    "hw_units",
                    "Devices placed",
                    tuple(float(v) for v in units),
                    "count",
                    revenue.units_month_1.source,
                    revenue.units_month_1.citation,
                ),
                Line(
                    "hw_asp",
                    "Realised ASP ($000/unit)",
                    tuple(float(v) / 1000.0 for v in realised_asp),
                    "money",
                    revenue.device_asp.source,
                    revenue.device_asp.citation,
                    channel_note,
                ),
                Line(
                    "hw_consumable_units",
                    "Consumables shipped",
                    tuple(float(v) for v in consumable_units),
                    "count",
                    revenue.consumables_per_device_per_year.source,
                    revenue.consumables_per_device_per_year.citation,
                    "Pulled through by the installed base, not by the year's placements.",
                ),
                Line(
                    "hw_consumable_price",
                    "Realised consumable price ($000)",
                    tuple(float(v) / 1000.0 for v in realised_consumable),
                    "money",
                    revenue.consumable_price.source,
                    revenue.consumable_price.citation,
                    channel_note,
                ),
            ),
        ),
        (
            Stream(
                "Device revenue",
                "hw_units",
                "Devices placed",
                "hw_asp",
                "Realised ASP ($000/unit)",
                "Device revenue",
            ),
            Stream(
                "Consumable revenue",
                "hw_consumable_units",
                "Consumables shipped",
                "hw_consumable_price",
                "Realised consumable price ($000)",
                "Consumable revenue",
            ),
        ),
    )


def _realisation(revenue: HardwareRevenue) -> float:
    direct = revenue.direct_sales_mix_pct.value / 100.0
    return direct + (1.0 - direct) * (1.0 - revenue.distributor_margin_pct.value / 100.0)


def _life_sciences(model: FinancialModel, n: int) -> tuple[Section, tuple[Stream, ...]]:
    revenue = model.assumptions.revenue
    assert isinstance(revenue, LifeSciencesRevenue)
    annual = np.asarray(model.pnl_annual["Revenue"])

    return (
        Section(
            "Revenue assumptions",
            (
                Line(
                    "ls_contracted",
                    "Contracted / partnership revenue",
                    tuple(float(v) / 1000.0 for v in annual),
                    "money",
                    revenue.partnership_upfront.source,
                    revenue.partnership_upfront.citation,
                    "Signed agreements only. No product revenue is assumed before approval.",
                ),
                Line(
                    "ls_units",
                    "Revenue-bearing agreements",
                    _repeat(1.0, n),
                    "count",
                    "derived",
                    None,
                    "Placeholder multiplier so the build reads consistently.",
                ),
            ),
        ),
        (
            Stream(
                "Contracted revenue",
                "ls_units",
                "Revenue-bearing agreements",
                "ls_contracted",
                "Contracted / partnership revenue",
                "Contracted revenue",
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# cost sections
# --------------------------------------------------------------------------- #


def _cogs(model: FinancialModel, n: int) -> Section:
    revenue = np.asarray(model.pnl_annual["Revenue"])
    cogs = np.asarray(model.pnl_annual["COGS"])
    rate = _safe_ratio(cogs, revenue)
    return Section(
        "Cost of goods sold assumptions",
        (
            Line(
                "cogs_pct",
                "Cost of revenue % of revenue",
                tuple(float(v) for v in rate),
                "percent",
                model.assumptions.cogs.target_gross_margin_pct.source,
                model.assumptions.cogs.target_gross_margin_pct.citation,
                "Falls over time where infrastructure scales sub-linearly with revenue.",
            ),
        ),
    )


def _active_functions(model: FinancialModel) -> list[str]:
    return [f for f in FUNCTIONS if any(v > 0 for v in model.headcount_annual.get(f, ()))]


def _headcount(model: FinancialModel, n: int) -> Section:
    """Headcount and the cost per head that reproduces the payroll exactly.

    Ending headcount is the figure everyone quotes and the one the one-pager shows,
    so it is what appears here. But payroll does not accrue on the year-end count --
    a hire who starts in month nine costs a quarter of a salary that year. The comp
    driver is therefore the *average cost per head actually incurred*, which is below
    full salary in any year with hiring in it, and above nothing in a year with none.

    Multiplying the two reproduces the engine's payroll to the dollar, which is what
    stops the workbook and the one-pager disagreeing.
    """
    plan = model.assumptions.headcount
    # The engine folds attrition-replacement cost into the loaded rate; the workbook
    # shows a single burden percentage, which is what a comp schedule normally carries.
    loaded = plan.loaded_multiplier.value * (1.0 + plan.annual_attrition_pct.value / 100.0 * 0.25)
    lines: list[Line] = []
    functions = _active_functions(model)

    for function in functions:
        lines.append(
            Line(
                f"hc_{function}",
                f"{function} headcount",
                tuple(float(v) for v in model.headcount_annual[function]),
                "count",
                plan.starting[function].source,  # type: ignore[index]
                plan.starting[function].citation,  # type: ignore[index]
                model.headcount_drivers.get(function),
            )
        )

    for function in functions:
        salary = plan.base_salary[function]  # type: ignore[index]
        headcount = np.asarray(model.headcount_annual[function], dtype=float)
        # Strip the burden back out: the P&L re-applies it, and applying it twice is
        # the classic way a model double-counts benefits.
        cash_cost = np.asarray(model.headcount_cost_annual[function], dtype=float) / loaded
        per_head = np.where(headcount > 0, _safe_ratio(cash_cost, headcount), salary.value)
        lines.append(
            Line(
                f"comp_{function}",
                f"{function} avg cash comp ($000)",
                tuple(float(v) / 1000.0 for v in per_head),
                "money",
                salary.source,
                salary.citation,
                f"Below the ${salary.value / 1000:,.0f}k full-year salary in any year "
                f"with hiring in it: a mid-year hire costs part of a year.",
            )
        )

    lines.append(
        Line(
            "burden_pct",
            "Payroll burden / benefits %",
            _repeat(loaded - 1.0, n),
            "percent",
            plan.loaded_multiplier.source,
            plan.loaded_multiplier.citation,
            "Employer taxes, benefits, equipment and software seats, plus the cost of "
            "replacing attrition.",
        )
    )
    return Section("Headcount & compensation assumptions", tuple(lines))


def _non_personnel(model: FinancialModel, n: int) -> Section:
    """Opex that is not payroll, split the way the P&L reports it."""
    opex = model.opex_components_annual
    lines: list[Line] = []
    groups = {
        "R&D non-personnel": ("R&D tooling & environments", "development programme"),
        "Sales & marketing programmes": ("marketing programmes",),
        "G&A non-personnel": (
            "facilities",
            "software & tooling",
            "legal, accounting, insurance & audit",
        ),
    }
    for label, keys in groups.items():
        total = np.zeros(n)
        for key in keys:
            if key in opex:
                total = total + np.asarray(opex[key])
        lines.append(
            Line(
                f"opex_{label}",
                label,
                tuple(float(v) / 1000.0 for v in total),
                "money",
                "derived",
                None,
            )
        )
    return Section("Non-personnel operating expense assumptions", tuple(lines))


def _cash(model: FinancialModel, n: int) -> Section:
    wc = model.assumptions.working_capital
    financing = model.assumptions.financing
    capex_actual = np.asarray(
        [
            sum(model.cash_monthly["Capex"][i * MONTHS_PER_YEAR : (i + 1) * MONTHS_PER_YEAR])
            for i in range(n)
        ]
    )

    raise_by_year = np.asarray(
        [
            sum(model.cash_monthly["Financing"][i * MONTHS_PER_YEAR : (i + 1) * MONTHS_PER_YEAR])
            for i in range(n)
        ]
    )

    return Section(
        "Cash flow & financing assumptions",
        (
            Line(
                "prepaid_pct",
                "Revenue billed annually in advance %",
                _repeat(_prepay(model), n),
                "percent",
                "derived",
                None,
                "Drives deferred revenue, which funds the company while the base grows.",
            ),
            Line(
                "tax_rate",
                "Blended tax rate %",
                _repeat(model.assumptions.tax.blended_rate_pct.value / 100.0, n),
                "percent",
                model.assumptions.tax.blended_rate_pct.source,
                model.assumptions.tax.blended_rate_pct.citation,
                "Charged only once accumulated losses are used up.",
            ),
            Line(
                "depreciation_years",
                "Capex useful life (years)",
                _repeat(model.assumptions.working_capital.capex_depreciation_years.value, n),
                "count",
                model.assumptions.working_capital.capex_depreciation_years.source,
                model.assumptions.working_capital.capex_depreciation_years.citation,
            ),
            Line(
                "capex",
                "Capital expenditure",
                tuple(abs(float(v)) / 1000.0 for v in capex_actual),
                "money",
                "derived",
            ),
            Line(
                "dso",
                "A/R days",
                _repeat(wc.dso_days.value, n),
                "count",
                wc.dso_days.source,
                wc.dso_days.citation,
            ),
            Line(
                "inventory_days",
                "Inventory days",
                _repeat(wc.inventory_days.value, n),
                "count",
                wc.inventory_days.source,
                wc.inventory_days.citation,
            ),
            Line(
                "dpo",
                "A/P days",
                _repeat(wc.dpo_days.value, n),
                "count",
                wc.dpo_days.source,
                wc.dpo_days.citation,
            ),
            Line(
                "opening_cash",
                "Opening cash",
                tuple([financing.cash_on_hand.value / 1000.0] + [0.0] * (n - 1)),
                "money",
                financing.cash_on_hand.source,
                financing.cash_on_hand.citation,
                financing.cash_on_hand.note,
            ),
            Line(
                "equity",
                "Equity financing",
                tuple(float(v) / 1000.0 for v in raise_by_year),
                "money",
                financing.raise_amount.source,
                financing.raise_amount.citation,
                financing.raise_amount.note,
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# the CFO observations
# --------------------------------------------------------------------------- #


def _prepay(model: FinancialModel) -> float:
    """Share of revenue billed a year up front. Zero outside subscription models."""
    revenue = model.assumptions.revenue
    if isinstance(revenue, SaaSRevenue):
        return revenue.annual_prepay_mix_pct.value / 100.0
    return 0.0


MINIMUM_CASH = 500_000.0
"""The buffer the liquidity line solves against. Stated on the sheet."""


def observations(model: FinancialModel) -> tuple[str, ...]:
    """The analytical payload of the Summary sheet.

    Each is one sentence and each says something a reader could act on. Only
    conditions that actually hold produce a line -- a fixed list of platitudes would
    train the reader to skip the block.
    """
    assumptions = model.assumptions
    coverage = assumptions.coverage
    notes: list[str] = []

    deck_claim = model.deck_final_year_revenue
    if deck_claim and model.final_year_revenue > 0:
        ratio = deck_claim / model.final_year_revenue
        if ratio > 1.6 or ratio < 0.6:
            notes.append(
                f"The company's own peak revenue projection of "
                f"${deck_claim / 1e6:,.1f}M is {ratio:,.0f}x this model's "
                f"${model.final_year_revenue / 1e6:,.1f}M. The gap is driven by "
                f"assumptions the deck did not state, and is the first thing to put "
                f"to the founder."
            )

    revenue = model.pnl_annual["Revenue"]
    if revenue[0] > 0 and revenue[-1] / max(revenue[0], 1.0) > 8:
        notes.append(
            f"Revenue grows {revenue[-1] / revenue[0]:,.0f}x across the horizon. "
            f"Treat the outer years as a pipeline and conversion case, not as "
            f"contracted backlog."
        )

    if coverage.deck_ratio < 0.5:
        notes.append(
            f"Only {coverage.from_deck} of {coverage.total} core inputs came from the "
            f"deck itself; the rest are benchmarks or derivations. The yellow-filled "
            f"rows on Assumptions are the ones to replace with company figures."
        )

    if assumptions.financing.cash_on_hand.value == 0:
        notes.append(
            "Opening cash is set to zero because the source materials did not state "
            "it. Replace it with the actual bank balance before using this for runway "
            "planning."
        )

    if model.runs_out_of_cash_month:
        notes.append(
            f"Cash goes negative in {model.runs_out_of_cash_month} and the peak "
            f"funding need is ${model.peak_cash_need / 1e6:,.1f}M. The raise in this "
            f"plan does not carry the company through the horizon."
        )
    elif model.ebitda_positive_month:
        notes.append(
            f"EBITDA turns positive in {model.ebitda_positive_month} and the plan "
            f"funds itself from there."
        )

    if assumptions.grounding_warning_count:
        notes.append(
            f"{assumptions.grounding_warning_count} figure(s) extracted from the deck "
            f"could not be confirmed on the page cited. Check them against the source "
            f"before relying on them."
        )

    return tuple(notes)
