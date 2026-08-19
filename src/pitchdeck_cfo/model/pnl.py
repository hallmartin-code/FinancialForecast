"""The P&L, from revenue down to net income.

EBITDA is where most startup models stop. Carrying on to net income is what makes
the statement credible to someone who reads real financials: depreciation on the
capex the model itself generates, interest, and tax that correctly pays nothing
until accumulated losses are used up.

The NOL carryforward matters more than it sounds. A model that taxes the first
profitable year at 25% understates the cash a company keeps by a wide margin, and
one that never taxes at all overstates it for the whole back half of the horizon.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pitchdeck_cfo.assume.schema import ModelAssumptions
from pitchdeck_cfo.model.cogs import COGSResult, gross_margin_pct, gross_profit
from pitchdeck_cfo.model.opex import OpexResult
from pitchdeck_cfo.model.revenue import RevenueResult
from pitchdeck_cfo.model.timeline import MONTHS_PER_YEAR, Timeline, Vector


@dataclass(frozen=True)
class PnLResult:
    revenue: Vector
    cogs: Vector
    gross_profit: Vector
    gross_margin_pct: Vector

    research_development: Vector
    sales_marketing: Vector
    general_admin: Vector
    total_opex: Vector

    ebitda: Vector
    ebitda_margin_pct: Vector

    depreciation: Vector
    ebit: Vector
    interest: Vector
    pretax_income: Vector
    tax: Vector
    net_income: Vector

    capex: Vector
    nol_balance: Vector

    def lines(self) -> dict[str, Vector]:
        """Every flow line, in statement order, for rendering."""
        return {
            "Revenue": self.revenue,
            "COGS": self.cogs,
            "Gross Profit": self.gross_profit,
            "R&D": self.research_development,
            "S&M": self.sales_marketing,
            "G&A": self.general_admin,
            "Total Opex": self.total_opex,
            "EBITDA": self.ebitda,
            "Depreciation": self.depreciation,
            "EBIT": self.ebit,
            "Interest": self.interest,
            "Pretax Income": self.pretax_income,
            "Tax": self.tax,
            "Net Income": self.net_income,
        }


def depreciation_schedule(capex: Vector, useful_life_years: float) -> Vector:
    """Straight-line depreciation of the capex the model itself generates.

    Each month's capex depreciates over `useful_life_years` beginning the month it is
    spent. Anything still depreciating past the horizon simply stops being counted.
    """
    months = capex.size
    life = max(1, round(useful_life_years * MONTHS_PER_YEAR))
    schedule = np.zeros(months)
    for t in range(months):
        if capex[t] <= 0:
            continue
        end = min(t + life, months)
        schedule[t:end] += capex[t] / life
    return schedule


def apply_tax(pretax: Vector, rate_pct: float, opening_nol: float) -> tuple[Vector, Vector]:
    """Tax after net operating losses, and the NOL balance each month.

    Losses accumulate and shelter later profits. Tax is charged only on income above
    the accumulated loss, which is why a model can be profitable for months before it
    pays anything.
    """
    months = pretax.size
    tax = np.zeros(months)
    balance = np.zeros(months)
    nol = opening_nol

    for t in range(months):
        income = pretax[t]
        if income <= 0:
            nol += -income
        else:
            shielded = min(income, nol)
            nol -= shielded
            taxable = income - shielded
            tax[t] = taxable * rate_pct / 100.0
        balance[t] = nol
    return tax, balance


def build(
    assumptions: ModelAssumptions,
    revenue: RevenueResult,
    cogs: COGSResult,
    opex: OpexResult,
    timeline: Timeline,
) -> PnLResult:
    """Assemble the statement. Every subtotal is computed, never restated."""
    gross = gross_profit(revenue.recognised, cogs.total)
    ebitda = gross - opex.total

    capex = revenue.recognised * assumptions.working_capital.capex_pct_of_revenue.value / 100.0
    depreciation = depreciation_schedule(
        capex, assumptions.working_capital.capex_depreciation_years.value
    )
    ebit = ebitda - depreciation

    # No debt is modelled unless a deck states an instrument, so interest is zero
    # rather than a plausible-looking placeholder.
    interest = timeline.zeros()
    pretax = ebit - interest

    tax, nol = apply_tax(
        pretax,
        assumptions.tax.blended_rate_pct.value,
        assumptions.tax.nol_carryforward_start.value,
    )

    return PnLResult(
        revenue=revenue.recognised,
        cogs=cogs.total,
        gross_profit=gross,
        gross_margin_pct=gross_margin_pct(revenue.recognised, gross),
        research_development=opex.research_development,
        sales_marketing=opex.sales_marketing,
        general_admin=opex.general_admin,
        total_opex=opex.total,
        ebitda=ebitda,
        ebitda_margin_pct=np.divide(
            ebitda,
            revenue.recognised,
            out=np.zeros_like(ebitda),
            where=revenue.recognised > 0,
        )
        * 100.0,
        depreciation=depreciation,
        ebit=ebit,
        interest=interest,
        pretax_income=pretax,
        tax=tax,
        net_income=pretax - tax,
        capex=capex,
        nol_balance=nol,
    )
