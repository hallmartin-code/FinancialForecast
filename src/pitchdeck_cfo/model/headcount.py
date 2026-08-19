"""The headcount plan -- the primary driver of operating expense.

Every hire here is *triggered* by something in the plan rather than assumed:

- Sales from quota coverage against the new revenue the plan requires, hired far
  enough ahead to cover the ramp.
- Customer Success from accounts per CSM.
- Engineering from revenue per engineer, floored at the roadmap ratio.
- Marketing from sales headcount; G&A from total headcount.
- Clinical, Regulatory, Quality, Research and Manufacturing hold at their stated
  starting level -- a deck that does not describe a clinical hiring plan gives no
  basis for inventing one.

Headcount only ratchets upward. Modelling a company that fires its way to
profitability is not what a founder's plan means, and attrition is handled as
replacement hiring inside the cost, not as a shrinking team.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pitchdeck_cfo.assume.schema import Function, ModelAssumptions
from pitchdeck_cfo.model.revenue import RevenueResult
from pitchdeck_cfo.model.timeline import MONTHS_PER_YEAR, Timeline, Vector

FUNCTIONS: tuple[Function, ...] = (
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

# Functions with no driver in the plan. They hold at their starting level rather
# than being given an invented growth curve.
STATIC_FUNCTIONS: tuple[Function, ...] = (
    "Product",
    "Design",
    "Clinical",
    "Regulatory",
    "Quality",
    "Manufacturing",
    "Research",
)


@dataclass(frozen=True)
class HeadcountResult:
    by_function: dict[Function, Vector]
    """Ending headcount each month."""

    total: Vector
    cost: Vector
    """Fully loaded monthly cost across all functions."""

    cost_by_function: dict[Function, Vector]
    hires: dict[Function, Vector]
    """Net additions each month. Derivation is shown, not just the result."""

    driver_notes: dict[Function, str]


def _ratchet(required: Vector, start: float) -> Vector:
    """A headcount line that never falls below where it has already been."""
    return np.maximum.accumulate(np.maximum(required, start))


def _sales_required(
    revenue: RevenueResult,
    timeline: Timeline,
    quota_annual: float,
    attainment_pct: float,
    ramp_months: int,
) -> Vector:
    """Reps needed to land the plan's new business, hired `ramp_months` early.

    Productive capacity per rep is quota x attainment, so a rep is never assumed to
    carry a full quota. Requirement is shifted earlier by the ramp: a rep who closes
    in month 18 has to be on the payroll in month 12.
    """
    effective_quota = quota_annual * attainment_pct / 100.0
    if effective_quota <= 0:
        return timeline.zeros()

    # Annualised run-rate of new business being won, smoothed over a quarter so a
    # single lumpy month does not hire a rep.
    new_annualised = revenue.new_recurring
    smoothed = np.convolve(new_annualised, np.ones(3) / 3.0, mode="same")
    required = smoothed / effective_quota

    if ramp_months <= 0:
        return required
    # Shift the requirement earlier; the tail holds at the final level.
    shifted = np.empty_like(required)
    shifted[:-ramp_months] = required[ramp_months:]
    shifted[-ramp_months:] = required[-1]
    return shifted


def build(
    assumptions: ModelAssumptions,
    revenue: RevenueResult,
    timeline: Timeline,
) -> HeadcountResult:
    """The headcount plan, function by function, with the trigger for each."""
    plan = assumptions.headcount
    months = timeline.months
    start = {function: plan.starting[function].value for function in FUNCTIONS}

    by_function: dict[Function, Vector] = {}
    notes: dict[Function, str] = {}

    for function in STATIC_FUNCTIONS:
        by_function[function] = np.full(months, start[function])
        notes[function] = "held at the level the deck states; no hiring driver given"

    # --- Sales: quota coverage ------------------------------------------------
    sales_required = _sales_required(
        revenue,
        timeline,
        plan.rep_quota_annual.value,
        plan.quota_attainment_pct.value,
        round(plan.rep_ramp_months.value),
    )
    by_function["Sales"] = _ratchet(np.ceil(sales_required), start["Sales"])
    notes["Sales"] = (
        f"new recurring revenue / (quota {plan.rep_quota_annual.value:,.0f} x "
        f"{plan.quota_attainment_pct.value:.0f}% attainment), hired "
        f"{plan.rep_ramp_months.value:.0f} months ahead of the ramp"
    )

    # --- Customer Success: accounts per CSM -----------------------------------
    per_csm = plan.accounts_per_csm.value
    if per_csm > 0:
        required = revenue.ending_customers / per_csm
        by_function["Customer Success"] = _ratchet(np.ceil(required), start["Customer Success"])
        notes["Customer Success"] = f"ending accounts / {per_csm:.0f} accounts per CSM"
    else:
        by_function["Customer Success"] = np.full(months, start["Customer Success"])
        notes["Customer Success"] = "no accounts-per-CSM ratio applies to this model"

    # --- Engineering: revenue per engineer, scaled sub-linearly ---------------
    #
    # Linear scaling is the reason most bottom-up models never reach profitability:
    # if every extra dollar of revenue needs a proportional engineer, R&D stays a
    # fixed share of revenue forever and the company never gets operating leverage.
    # Headcount is therefore anchored at the first revenue-bearing month and grows
    # with revenue^exponent from there. At exponent 1.0 this is exactly linear again,
    # which is the honest output for a plan that really does hire that way.
    roadmap_floor = plan.engineers_per_product_line.value * plan.product_lines.value
    annualised_revenue = revenue.recognised * MONTHS_PER_YEAR
    per_engineer = plan.revenue_per_engineer.value
    exponent = plan.scale_exponent.value

    required_eng = np.zeros(months)
    if per_engineer > 0:
        earning = annualised_revenue[annualised_revenue > 0]
        if earning.size:
            reference = float(earning[0])
            anchor = reference / per_engineer
            ratio = np.divide(
                annualised_revenue,
                reference,
                out=np.zeros(months),
                where=annualised_revenue > 0,
            )
            required_eng = anchor * np.power(ratio, exponent)

    by_function["Engineering"] = _ratchet(
        np.ceil(np.maximum(required_eng, roadmap_floor)), start["Engineering"]
    )
    notes["Engineering"] = (
        f"greater of the roadmap floor ({roadmap_floor:.0f} FTE) and "
        f"revenue / {per_engineer:,.0f} per engineer, scaled at revenue^{exponent:g}"
    )

    # --- Marketing and G&A: ratios to the teams they support ------------------
    per_marketing = plan.sales_per_marketing_hire.value
    marketing_required = (
        by_function["Sales"] / per_marketing if per_marketing > 0 else np.zeros(months)
    )
    by_function["Marketing"] = _ratchet(np.ceil(marketing_required), start["Marketing"])
    notes["Marketing"] = f"sales headcount / {per_marketing:.0f} per marketing hire"

    supported = np.sum([by_function[f] for f in FUNCTIONS if f != "G&A"], axis=0)
    per_ga = plan.ftes_per_ga_hire.value
    # A zero ratio means "no G&A hiring driver", not "divide by zero". Left ungated
    # this produced NaN, which propagated silently through the entire statement --
    # every accounting tie compares NaN to NaN and fails, which is how it was caught.
    ga_required = supported / per_ga if per_ga > 0 else np.zeros(months)
    by_function["G&A"] = _ratchet(np.ceil(ga_required), start["G&A"])
    notes["G&A"] = (
        f"company headcount / {per_ga:.0f} FTE per G&A hire"
        if per_ga > 0
        else "held at the level the deck states; no G&A hiring ratio given"
    )

    # --- cost -----------------------------------------------------------------
    multiplier = plan.loaded_multiplier.value
    # Attrition is replacement hiring, which costs recruiting and lost ramp rather
    # than shrinking the team. Carried as a uplift on loaded cost.
    attrition_uplift = 1.0 + plan.annual_attrition_pct.value / 100.0 * 0.25

    cost_by_function: dict[Function, Vector] = {}
    hires: dict[Function, Vector] = {}
    for function in FUNCTIONS:
        salary_monthly = plan.base_salary[function].value / MONTHS_PER_YEAR
        cost_by_function[function] = (
            by_function[function] * salary_monthly * multiplier * attrition_uplift
        )
        hires[function] = np.diff(by_function[function], prepend=start[function])

    total = np.sum([by_function[f] for f in FUNCTIONS], axis=0)
    cost = np.sum([cost_by_function[f] for f in FUNCTIONS], axis=0)

    return HeadcountResult(
        by_function=by_function,
        total=total,
        cost=cost,
        cost_by_function=cost_by_function,
        hires=hires,
        driver_notes=notes,
    )
