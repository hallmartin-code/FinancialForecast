"""Orchestration: `ModelAssumptions` in, `FinancialModel` out.

`FinancialModel` is the single object both renderers consume, which is what makes it
impossible for the PDF and the workbook to disagree. It carries monthly series and
their annual aggregates together, so nothing downstream has to re-derive a total and
risk deriving it differently.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict

from pitchdeck_cfo.assume.schema import ModelAssumptions
from pitchdeck_cfo.model import cashflow, cogs, headcount, metrics, opex, pnl, revenue
from pitchdeck_cfo.model.timeline import Timeline, Vector


class IntegrityError(Exception):
    """The statement does not tie. A model that does not reconcile is not shippable."""


# Accounting ties are checked to the dollar, not to a relative tolerance. numpy's
# default rtol of 1e-5 sounds strict and is not: on a $30M cash balance it silently
# accepts a $300 discrepancy, and the size of the error it tolerates grows with the
# company. A dollar of absolute slack covers float accumulation over sixty months and
# nothing else.
MONEY_ATOL = 1.0
MONEY_RTOL = 0.0


def ties(left: Vector, right: Vector) -> bool:
    """Do two money series agree to the dollar?"""
    return bool(np.allclose(left, right, rtol=MONEY_RTOL, atol=MONEY_ATOL))


class FinancialModel(BaseModel):
    """A complete, reconciled financial model."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    assumptions: ModelAssumptions

    month_labels: tuple[str, ...]
    year_labels: tuple[str, ...]

    pnl_monthly: dict[str, tuple[float, ...]]
    pnl_annual: dict[str, tuple[float, ...]]

    cash_monthly: dict[str, tuple[float, ...]]
    cash_annual: dict[str, tuple[float, ...]]

    headcount_monthly: dict[str, tuple[float, ...]]
    headcount_annual: dict[str, tuple[float, ...]]
    headcount_cost_annual: dict[str, tuple[float, ...]]
    headcount_drivers: dict[str, str]

    revenue_components_annual: dict[str, tuple[float, ...]]
    customers_annual: tuple[float, ...] = ()
    """Average paying customers per year -- the base that actually earned the revenue."""
    cogs_components_annual: dict[str, tuple[float, ...]]
    opex_components_annual: dict[str, tuple[float, ...]]

    metrics: tuple[metrics.YearMetrics, ...]
    break_even: dict[str, str]

    peak_cash_need: float
    peak_cash_month: str | None
    ebitda_positive_month: str | None
    cash_positive_month: str | None
    runs_out_of_cash_month: str | None

    @property
    def horizon_years(self) -> int:
        return len(self.year_labels)

    @property
    def final_year_revenue(self) -> float:
        return self.pnl_annual["Revenue"][-1]

    @property
    def deck_final_year_revenue(self) -> float | None:
        """The last year of the company's own revenue forecast, if it gave one.

        Kept strictly separate from the model's own number. The one-pager compares
        them and says so when they disagree, because a partner reading a model that
        is an order of magnitude below the deck needs that on the page.
        """
        projection = self.assumptions.deck_revenue_projection
        if not projection:
            return None
        return max(value for _, value in projection)

    @property
    def final_year_ebitda_margin(self) -> float | None:
        revenue_value = self.final_year_revenue
        if revenue_value == 0:
            return None
        return self.pnl_annual["EBITDA"][-1] / revenue_value * 100.0

    @property
    def ending_cash(self) -> tuple[float, ...]:
        return self.cash_annual["Ending Cash"]


def _to_tuple(vector: Vector) -> tuple[float, ...]:
    return tuple(float(x) for x in vector)


def _annualise(
    lines: dict[str, Vector], timeline: Timeline, balances: frozenset[str] = frozenset()
) -> dict[str, tuple[float, ...]]:
    """Sum flows into years; take the closing value of balances.

    Summing a balance across twelve months produces a number with no meaning, so the
    two are handled separately and the caller names which is which.
    """
    out: dict[str, tuple[float, ...]] = {}
    for name, series in lines.items():
        aggregated = (
            timeline.end_of_year(series) if name in balances else timeline.to_annual(series)
        )
        out[name] = _to_tuple(aggregated)
    return out


# Balance-sheet-like lines within the cash statement.
CASH_BALANCES = frozenset({"Ending Cash"})
# Ratio lines cannot be summed; they are recomputed from their annual components.
PNL_RATIOS = frozenset({"Gross Margin %", "EBITDA Margin %"})


def check_integrity(
    result: pnl.PnLResult, cash: cashflow.CashflowResult, opening_cash: float
) -> None:
    """Assert the statement reconciles. Raises rather than shipping a broken model."""
    # A NaN compares unequal to everything, including itself, so it would surface as
    # some downstream tie failure with a misleading message. Name it directly.
    for label, series in result.lines().items():
        if not np.all(np.isfinite(series)):
            raise IntegrityError(f"the {label} line contains a non-finite value")

    if not ties(result.revenue - result.cogs, result.gross_profit):
        raise IntegrityError("revenue - COGS does not equal gross profit")
    if not ties(result.gross_profit - result.total_opex, result.ebitda):
        raise IntegrityError("gross profit - opex does not equal EBITDA")
    if not ties(
        result.research_development + result.sales_marketing + result.general_admin,
        result.total_opex,
    ):
        raise IntegrityError("the opex lines do not sum to total opex")
    if not ties(result.ebitda - result.depreciation, result.ebit):
        raise IntegrityError("EBITDA - depreciation does not equal EBIT")
    if not ties(result.pretax_income - result.tax, result.net_income):
        raise IntegrityError("pretax income - tax does not equal net income")
    if not cash.ties_to_balance(opening_cash):
        raise IntegrityError("the cash flow statement does not tie to the cash balance")
    if np.any(result.tax < -1e-9):
        raise IntegrityError("tax is negative; the NOL carryforward is refunding cash")


def build(assumptions: ModelAssumptions) -> FinancialModel:
    """Run every stage and return a reconciled model."""
    timeline = Timeline(
        start_month=assumptions.company.start_month,
        horizon_years=assumptions.company.horizon_years,
    )

    revenue_result = revenue.build(assumptions, timeline)

    program = None
    if assumptions.company.business_model == "life_sciences":
        program = revenue.program_spend(assumptions.revenue, timeline)  # type: ignore[arg-type]

    cogs_result = cogs.build(assumptions, revenue_result, timeline)
    headcount_result = headcount.build(assumptions, revenue_result, timeline)
    opex_result = opex.build(
        assumptions, headcount_result, revenue_result, timeline, program_spend=program
    )
    pnl_result = pnl.build(assumptions, revenue_result, cogs_result, opex_result, timeline)
    cash_result = cashflow.build(assumptions, revenue_result, pnl_result, timeline)

    check_integrity(pnl_result, cash_result, assumptions.financing.cash_on_hand.value)

    turns = cashflow.break_even(pnl_result, cash_result)
    labels = timeline.month_labels()

    pnl_lines = pnl_result.lines()
    pnl_annual = _annualise(pnl_lines, timeline)
    # Ratios are recomputed from annual totals rather than averaged from months --
    # the average of twelve monthly margins is not the annual margin.
    annual_revenue = np.asarray(pnl_annual["Revenue"])
    with np.errstate(divide="ignore", invalid="ignore"):
        pnl_annual["Gross Margin %"] = _to_tuple(
            np.divide(
                np.asarray(pnl_annual["Gross Profit"]),
                annual_revenue,
                out=np.zeros_like(annual_revenue),
                where=annual_revenue > 0,
            )
            * 100.0
        )
        pnl_annual["EBITDA Margin %"] = _to_tuple(
            np.divide(
                np.asarray(pnl_annual["EBITDA"]),
                annual_revenue,
                out=np.zeros_like(annual_revenue),
                where=annual_revenue > 0,
            )
            * 100.0
        )

    cash_lines = cash_result.lines()
    metric_rows = metrics.build(
        assumptions, revenue_result, pnl_result, cash_result, headcount_result.total, timeline
    )

    def label_or_none(index: int | None) -> str | None:
        return labels[index] if index is not None else None

    return FinancialModel(
        assumptions=assumptions,
        month_labels=labels,
        year_labels=timeline.year_labels(),
        pnl_monthly={name: _to_tuple(series) for name, series in pnl_lines.items()},
        pnl_annual=pnl_annual,
        cash_monthly={name: _to_tuple(series) for name, series in cash_lines.items()},
        cash_annual=_annualise(cash_lines, timeline, balances=CASH_BALANCES),
        headcount_monthly={
            str(name): _to_tuple(series) for name, series in headcount_result.by_function.items()
        }
        | {"Total": _to_tuple(headcount_result.total)},
        headcount_annual={
            str(name): _to_tuple(timeline.end_of_year(series))
            for name, series in headcount_result.by_function.items()
        }
        | {"Total": _to_tuple(timeline.end_of_year(headcount_result.total))},
        headcount_cost_annual={
            str(name): _to_tuple(timeline.to_annual(series))
            for name, series in headcount_result.cost_by_function.items()
        }
        | {"Total": _to_tuple(timeline.to_annual(headcount_result.cost))},
        headcount_drivers={str(k): v for k, v in headcount_result.driver_notes.items()},
        revenue_components_annual={
            name: _to_tuple(timeline.to_annual(series))
            for name, series in revenue_result.components.items()
        },
        customers_annual=_to_tuple(timeline.average_of_year(revenue_result.ending_customers)),
        cogs_components_annual={
            name: _to_tuple(timeline.to_annual(series))
            for name, series in cogs_result.components.items()
        },
        opex_components_annual={
            name: _to_tuple(timeline.to_annual(series))
            for name, series in opex_result.components.items()
        },
        metrics=metric_rows,
        break_even=turns.describe(labels),
        peak_cash_need=turns.peak_cash_need,
        peak_cash_month=label_or_none(turns.peak_cash_need_month),
        ebitda_positive_month=label_or_none(turns.ebitda_positive_month),
        cash_positive_month=label_or_none(turns.cash_flow_positive_month),
        runs_out_of_cash_month=label_or_none(turns.runs_out_of_cash_month),
    )


def annual_frame(model: FinancialModel) -> Any:
    """The annual P&L as a pandas DataFrame, for terminal rendering and tests."""
    import pandas as pd

    return pd.DataFrame(model.pnl_annual, index=list(model.year_labels)).T
