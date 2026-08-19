"""Cash flow, indirect method, and the facts only a monthly model can state.

Peak cash need, the month cash flow turns positive, and months of runway from the
raise are all month-level facts. An annual model cannot produce them: a company that
dips to its low point in month 7 and recovers by month 12 looks fine in an annual
bucket and is out of money in reality.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pitchdeck_cfo.assume.schema import ModelAssumptions
from pitchdeck_cfo.model.pnl import PnLResult
from pitchdeck_cfo.model.revenue import RevenueResult
from pitchdeck_cfo.model.timeline import MONTHS_PER_YEAR, Timeline, Vector, first_index_where

DAYS_PER_MONTH = 30.0


@dataclass(frozen=True)
class CashflowResult:
    ebitda: Vector
    change_in_receivables: Vector
    change_in_deferred_revenue: Vector
    change_in_payables: Vector
    change_in_inventory: Vector
    working_capital_change: Vector

    capex: Vector
    tax_paid: Vector
    financing: Vector

    net_change: Vector
    ending_cash: Vector

    receivables: Vector
    payables: Vector
    inventory: Vector

    def lines(self) -> dict[str, Vector]:
        return {
            "EBITDA": self.ebitda,
            "Change in AR": self.change_in_receivables,
            "Change in Deferred Revenue": self.change_in_deferred_revenue,
            "Change in AP": self.change_in_payables,
            "Change in Inventory": self.change_in_inventory,
            "Capex": -self.capex,
            "Tax Paid": -self.tax_paid,
            "Financing": self.financing,
            "Net Change in Cash": self.net_change,
            "Ending Cash": self.ending_cash,
        }

    def ties_to_balance(self, opening_cash: float) -> bool:
        """The statement must explain every dollar of movement in the balance.

        Checked to the dollar rather than to a relative tolerance: numpy's default
        rtol would accept a discrepancy that scales with the size of the balance.
        """
        expected = opening_cash + np.cumsum(self.net_change)
        return bool(np.allclose(expected, self.ending_cash, rtol=0.0, atol=1.0))


def _balance_from_days(flow_monthly: Vector, days: float) -> Vector:
    """A balance implied by a days-outstanding assumption on a monthly flow."""
    return flow_monthly * (days / DAYS_PER_MONTH)


def build(
    assumptions: ModelAssumptions,
    revenue: RevenueResult,
    pnl: PnLResult,
    timeline: Timeline,
) -> CashflowResult:
    """EBITDA to ending cash, with working capital derived from the model's own flows."""
    wc = assumptions.working_capital
    financing_settings = assumptions.financing

    receivables = _balance_from_days(revenue.billings, wc.dso_days.value)
    payables = _balance_from_days(pnl.cogs + pnl.total_opex, wc.dpo_days.value)
    inventory = _balance_from_days(pnl.cogs, wc.inventory_days.value)
    deferred = revenue.deferred_balance

    # An increase in an asset consumes cash; an increase in a liability releases it.
    change_ar = -np.diff(receivables, prepend=0.0)
    change_ap = np.diff(payables, prepend=0.0)
    change_inv = -np.diff(inventory, prepend=0.0)
    change_deferred = np.diff(deferred, prepend=0.0)

    working_capital_change = change_ar + change_ap + change_inv + change_deferred

    financing = timeline.zeros()
    raise_month = round(financing_settings.raise_month_offset.value)
    if 0 <= raise_month < timeline.months:
        financing[raise_month] += financing_settings.raise_amount.value

    net_change = pnl.ebitda + working_capital_change - pnl.capex - pnl.tax + financing
    ending_cash = financing_settings.cash_on_hand.value + np.cumsum(net_change)

    return CashflowResult(
        ebitda=pnl.ebitda,
        change_in_receivables=change_ar,
        change_in_deferred_revenue=change_deferred,
        change_in_payables=change_ap,
        change_in_inventory=change_inv,
        working_capital_change=working_capital_change,
        capex=pnl.capex,
        tax_paid=pnl.tax,
        financing=financing,
        net_change=net_change,
        ending_cash=ending_cash,
        receivables=receivables,
        payables=payables,
        inventory=inventory,
    )


@dataclass(frozen=True)
class BreakEven:
    """When the model turns, stated honestly when it does not."""

    ebitda_positive_month: int | None
    cash_flow_positive_month: int | None
    peak_cash_need: float
    peak_cash_need_month: int | None
    runs_out_of_cash_month: int | None
    months_of_runway: int | None

    def describe(self, month_labels: tuple[str, ...]) -> dict[str, str]:
        def label(index: int | None) -> str:
            if index is None:
                return "not within the horizon"
            return f"{month_labels[index]} (month {index + 1})"

        return {
            "EBITDA positive": label(self.ebitda_positive_month),
            "Cash flow positive": label(self.cash_flow_positive_month),
            "Peak cash need": f"{self.peak_cash_need:,.0f} at {label(self.peak_cash_need_month)}",
            "Cash exhausted": label(self.runs_out_of_cash_month),
        }


def break_even(pnl: PnLResult, cash: CashflowResult) -> BreakEven:
    """Find the turning points, or report plainly that there are none.

    Sustained rather than momentary: a single positive month inside a losing year is
    noise, so a turn only counts when it holds for three consecutive months.
    """
    ebitda_month = _sustained(pnl.ebitda > 0)
    cash_month = _sustained(cash.net_change > 0)

    trough = float(np.min(cash.ending_cash))
    trough_month = int(np.argmin(cash.ending_cash)) if cash.ending_cash.size else None
    peak_need = max(0.0, -trough)

    out_of_cash = first_index_where(cash.ending_cash, cash.ending_cash < 0)
    runway = out_of_cash if out_of_cash is not None else None

    return BreakEven(
        ebitda_positive_month=ebitda_month,
        cash_flow_positive_month=cash_month,
        peak_cash_need=peak_need,
        peak_cash_need_month=trough_month,
        runs_out_of_cash_month=out_of_cash,
        months_of_runway=runway,
    )


def _sustained(flags: np.ndarray, window: int = 3) -> int | None:
    """First index from which `flags` stays true for `window` months."""
    if flags.size < window:
        return None
    rolling = np.convolve(flags.astype(float), np.ones(window), mode="valid")
    hits = np.flatnonzero(rolling >= window)
    return int(hits[0]) if hits.size else None


def months_of_runway_from(cash: CashflowResult, from_month: int = 0) -> int | None:
    """Months until cash runs out, counted from a given month."""
    if cash.ending_cash.size == 0:
        return None
    negative = first_index_where(cash.ending_cash, cash.ending_cash < 0)
    if negative is None:
        return None
    return max(0, negative - from_month)


def annualised_burn(cash: CashflowResult, timeline: Timeline) -> Vector:
    """Average monthly net cash movement per fiscal year."""
    return timeline.average_of_year(cash.net_change) * MONTHS_PER_YEAR
