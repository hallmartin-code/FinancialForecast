"""Metrics computed from the model, never restated from the deck.

That distinction is the whole point of this module. A deck's CAC is a claim; a CAC
computed from the sales and marketing the model actually spends and the customers it
actually wins is a result, and the two disagreeing is itself information the
one-pager reports.

Every metric returns None where it is not defined -- no customers won means no CAC,
and printing zero or a divide-by-zero infinity would both be lies.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pitchdeck_cfo.assume.schema import ModelAssumptions, SaaSRevenue
from pitchdeck_cfo.model.cashflow import CashflowResult
from pitchdeck_cfo.model.pnl import PnLResult
from pitchdeck_cfo.model.revenue import RevenueResult
from pitchdeck_cfo.model.timeline import MONTHS_PER_YEAR, Timeline


@dataclass(frozen=True)
class YearMetrics:
    year: int
    revenue: float
    revenue_growth_pct: float | None
    gross_margin_pct: float | None
    ebitda_margin_pct: float | None

    cac: float | None
    ltv: float | None
    ltv_to_cac: float | None
    cac_payback_months: float | None

    magic_number: float | None
    burn_multiple: float | None
    rule_of_40: float | None
    net_revenue_retention_pct: float | None

    revenue_per_fte: float | None
    ending_headcount: float
    ending_cash: float


def _safe(numerator: float, denominator: float) -> float | None:
    """A ratio, or None when the denominator makes it meaningless."""
    if denominator == 0 or not np.isfinite(denominator):
        return None
    value = numerator / denominator
    return float(value) if np.isfinite(value) else None


def build(
    assumptions: ModelAssumptions,
    revenue: RevenueResult,
    pnl: PnLResult,
    cash: CashflowResult,
    headcount_total: np.ndarray,
    timeline: Timeline,
) -> tuple[YearMetrics, ...]:
    """One row of metrics per fiscal year."""
    annual_revenue = timeline.to_annual(pnl.revenue)
    annual_gross = timeline.to_annual(pnl.gross_profit)
    annual_ebitda = timeline.to_annual(pnl.ebitda)
    annual_sm = timeline.to_annual(pnl.sales_marketing)
    annual_new_customers = timeline.to_annual(revenue.new_customers)
    annual_new_recurring = timeline.to_annual(revenue.new_recurring)
    ending_headcount = timeline.end_of_year(headcount_total)
    ending_cash = timeline.end_of_year(cash.ending_cash)
    ending_customers = timeline.end_of_year(revenue.ending_customers)

    churn_pct = _annual_logo_churn(assumptions)

    rows: list[YearMetrics] = []
    for year in range(timeline.horizon_years):
        prior_revenue = annual_revenue[year - 1] if year > 0 else 0.0

        # These fields are named _pct and must hold percentages. _safe returns a
        # ratio, so the conversion happens here rather than at each use site.
        gross_ratio = _safe(annual_gross[year], annual_revenue[year])
        ebitda_ratio = _safe(annual_ebitda[year], annual_revenue[year])
        gross_margin = gross_ratio * 100.0 if gross_ratio is not None else None
        ebitda_margin = ebitda_ratio * 100.0 if ebitda_ratio is not None else None
        growth = _safe(annual_revenue[year] - prior_revenue, prior_revenue)

        # CAC: everything spent to acquire, over what was acquired.
        cac = _safe(annual_sm[year], annual_new_customers[year])

        # LTV on gross profit, not revenue -- a customer is only worth what they
        # contribute after the cost of serving them.
        arpu = _safe(annual_revenue[year], ending_customers[year]) or 0.0
        lifetime_years = _safe(1.0, churn_pct / 100.0) if churn_pct > 0 else None
        ltv = (
            arpu * (gross_margin or 0.0) / 100.0 * lifetime_years
            if lifetime_years is not None
            else None
        )

        payback = None
        if cac is not None and gross_margin is not None and arpu > 0:
            monthly_gross_per_customer = arpu * gross_margin / 100.0 / MONTHS_PER_YEAR
            payback = _safe(cac, monthly_gross_per_customer)

        # Magic number: new ARR won this year over the prior year's S&M spend.
        magic = _safe(annual_new_recurring[year], annual_sm[year - 1]) if year > 0 else None

        # Burn multiple: cash burned per dollar of new recurring revenue.
        burned = -min(0.0, float(timeline.to_annual(cash.net_change)[year]))
        burn_multiple = _safe(burned, annual_new_recurring[year]) if burned > 0 else None

        rule_of_40 = (
            (growth * 100.0 + ebitda_margin)
            if growth is not None and ebitda_margin is not None
            else None
        )

        rows.append(
            YearMetrics(
                year=year + 1,
                revenue=float(annual_revenue[year]),
                revenue_growth_pct=growth * 100.0 if growth is not None else None,
                gross_margin_pct=gross_margin,
                ebitda_margin_pct=ebitda_margin,
                cac=cac,
                ltv=ltv,
                ltv_to_cac=_safe(ltv, cac) if ltv is not None and cac else None,
                cac_payback_months=payback,
                magic_number=magic,
                burn_multiple=burn_multiple,
                rule_of_40=rule_of_40,
                net_revenue_retention_pct=_net_revenue_retention(assumptions),
                revenue_per_fte=_safe(annual_revenue[year], ending_headcount[year]),
                ending_headcount=float(ending_headcount[year]),
                ending_cash=float(ending_cash[year]),
            )
        )
    return tuple(rows)


def _annual_logo_churn(assumptions: ModelAssumptions) -> float:
    """Logo churn, which only the SaaS engine has. Zero elsewhere means no decay."""
    revenue = assumptions.revenue
    if isinstance(revenue, SaaSRevenue):
        return float(revenue.logo_churn_annual_pct.value)
    return 0.0


def _net_revenue_retention(assumptions: ModelAssumptions) -> float | None:
    """NRR is a subscription concept. None rather than a number for the others."""
    revenue = assumptions.revenue
    if isinstance(revenue, SaaSRevenue):
        return float(revenue.net_revenue_retention_pct.value)
    return None
