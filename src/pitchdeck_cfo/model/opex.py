"""Operating expense: R&D, Sales & Marketing, and G&A.

Each is headcount cost plus its own non-headcount lines. The one that usually gets
faked is marketing: a flat percentage of revenue is easy and meaningless. Here
programme spend is tied to the new business the plan requires, which makes CAC an
output of the model rather than a number copied off a slide.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pitchdeck_cfo.assume.schema import Function, ModelAssumptions
from pitchdeck_cfo.model.headcount import HeadcountResult
from pitchdeck_cfo.model.revenue import RevenueResult
from pitchdeck_cfo.model.timeline import MONTHS_PER_YEAR, Timeline, Vector

RD_FUNCTIONS: tuple[Function, ...] = (
    "Engineering",
    "Product",
    "Design",
    "Research",
    "Clinical",
    "Regulatory",
    "Quality",
)
SM_FUNCTIONS: tuple[Function, ...] = ("Sales", "Marketing", "Customer Success")
GA_FUNCTIONS: tuple[Function, ...] = ("G&A", "Manufacturing")


@dataclass(frozen=True)
class OpexResult:
    research_development: Vector
    sales_marketing: Vector
    general_admin: Vector
    total: Vector
    components: dict[str, Vector]

    def check_sums_to_total(self) -> bool:
        parts = self.research_development + self.sales_marketing + self.general_admin
        return bool(np.allclose(parts, self.total))


def build(
    assumptions: ModelAssumptions,
    headcount: HeadcountResult,
    revenue: RevenueResult,
    timeline: Timeline,
    program_spend: Vector | None = None,
) -> OpexResult:
    """Operating expense by category.

    `program_spend` carries a life-sciences development programme, which is R&D that
    has nothing to do with headcount -- CRO fees, tox studies, manufacturing runs.
    """
    settings = assumptions.opex
    months = timeline.months

    def cost_of(functions: tuple[Function, ...]) -> Vector:
        return np.sum([headcount.cost_by_function[f] for f in functions], axis=0)

    components: dict[str, Vector] = {}

    # --- R&D ------------------------------------------------------------------
    rd_people = cost_of(RD_FUNCTIONS)
    engineers = headcount.by_function["Engineering"] + headcount.by_function["Research"]
    rd_tooling = engineers * settings.rd_tooling_per_engineer.value / MONTHS_PER_YEAR
    rd_program = program_spend if program_spend is not None else np.zeros(months)

    research_development = rd_people + rd_tooling + rd_program
    components["R&D personnel"] = rd_people
    components["R&D tooling & environments"] = rd_tooling
    if rd_program.any():
        components["development programme"] = rd_program

    # --- Sales & Marketing ----------------------------------------------------
    sm_people = cost_of(SM_FUNCTIONS)
    # Programme spend follows the new business being won, not total revenue. A
    # company holding a flat book spends nothing on acquisition under this rule,
    # which is the point.
    # new_recurring is the annualised value of what was signed *in that month*, so it
    # is already a monthly increment. No further scaling.
    marketing_programme = (
        revenue.new_recurring * settings.marketing_pct_of_new_revenue.value / 100.0
    )
    sales_marketing = sm_people + marketing_programme
    components["S&M personnel"] = sm_people
    components["marketing programmes"] = marketing_programme

    # --- G&A ------------------------------------------------------------------
    ga_people = cost_of(GA_FUNCTIONS)
    facilities = headcount.total * settings.rent_per_fte.value / MONTHS_PER_YEAR
    software = headcount.total * settings.software_per_fte.value / MONTHS_PER_YEAR
    ga_fixed = np.full(months, settings.ga_fixed_annual.value / MONTHS_PER_YEAR)

    general_admin = ga_people + facilities + software + ga_fixed
    components["G&A personnel"] = ga_people
    components["facilities"] = facilities
    components["software & tooling"] = software
    components["legal, accounting, insurance & audit"] = ga_fixed

    return OpexResult(
        research_development=research_development,
        sales_marketing=sales_marketing,
        general_admin=general_admin,
        total=research_development + sales_marketing + general_admin,
        components=components,
    )
