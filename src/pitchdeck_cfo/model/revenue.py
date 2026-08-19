"""Revenue, built from drivers rather than asserted as a growth curve.

One engine per supported business model. Each returns the same `RevenueResult`, so
everything downstream — COGS, headcount triggers, working capital — is written once.

All functions here are pure: assumptions in, arrays out, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from pitchdeck_cfo.assume.schema import (
    HardwareRevenue,
    LifeSciencesRevenue,
    ModelAssumptions,
    SaaSRevenue,
)
from pitchdeck_cfo.model.timeline import (
    MONTHS_PER_YEAR,
    Timeline,
    Vector,
    annual_to_monthly_rate,
    decaying_growth_curve,
)


@dataclass(frozen=True)
class RevenueResult:
    """What every revenue engine produces."""

    recognised: Vector
    """Revenue recognised in the month. This is the P&L line."""

    billings: Vector
    """Cash-basis invoicing. Differs from recognised wherever there is prepayment."""

    new_customers: Vector
    ending_customers: Vector

    new_recurring: Vector = field(default_factory=lambda: np.zeros(0))
    """New recurring revenue won in the month, annualised. Drives sales hiring and CAC."""

    deferred_balance: Vector = field(default_factory=lambda: np.zeros(0))

    components: dict[str, Vector] = field(default_factory=dict)
    """Named sub-lines, e.g. device vs consumable. Rendered in the workbook."""

    def __post_init__(self) -> None:
        if self.new_recurring.size == 0:
            object.__setattr__(self, "new_recurring", np.zeros_like(self.recognised))
        if self.deferred_balance.size == 0:
            object.__setattr__(self, "deferred_balance", np.zeros_like(self.recognised))


# --------------------------------------------------------------------------- #
# SaaS -- cohort build
# --------------------------------------------------------------------------- #


def saas(revenue: SaaSRevenue, timeline: Timeline) -> RevenueResult:
    """New logos to gross new ARR, existing base carried forward at NRR.

    NRR is applied to the existing base rather than modelling gross churn and
    expansion as separate lines. That is deliberate: NRR is the figure decks actually
    state, and splitting it would mean inventing the two halves. Logo churn is tracked
    separately because customer count drives customer-success hiring, and a company
    can lose logos while growing revenue.
    """
    months = timeline.months
    arpu = revenue.arpu_annual.value
    nrr_monthly = (revenue.net_revenue_retention_pct.value / 100.0) ** (1.0 / MONTHS_PER_YEAR)
    logo_churn_monthly = annual_to_monthly_rate(-revenue.logo_churn_annual_pct.value)

    new_logos = revenue.new_logos_month_1.value * decaying_growth_curve(
        months,
        revenue.new_logo_growth_monthly_pct.value,
        revenue.growth_decay_annual_pct.value,
        revenue.terminal_growth_monthly_pct.value,
    )

    arr = np.zeros(months)
    customers = np.zeros(months)
    new_arr = new_logos * arpu

    previous_arr = revenue.starting_arr.value
    previous_customers = revenue.starting_customers.value
    for t in range(months):
        arr[t] = previous_arr * nrr_monthly + new_arr[t]
        customers[t] = previous_customers * (1.0 + logo_churn_monthly) + new_logos[t]
        previous_arr, previous_customers = arr[t], customers[t]

    recognised = arr / MONTHS_PER_YEAR

    # Annual prepayment bills a year up front and recognises it ratably. At steady
    # state roughly half of the prepaid year is still unearned, which is the standard
    # approximation and is documented as such rather than modelled cohort by cohort.
    prepay = revenue.annual_prepay_mix_pct.value / 100.0
    deferred = arr * prepay * 0.5
    billings = recognised + np.diff(deferred, prepend=deferred[0] if months else 0.0)

    return RevenueResult(
        recognised=recognised,
        billings=billings,
        new_customers=new_logos,
        ending_customers=customers,
        new_recurring=new_arr,
        deferred_balance=deferred,
        components={"subscription": recognised},
    )


# --------------------------------------------------------------------------- #
# Life sciences -- milestone driven, usually no product revenue at all
# --------------------------------------------------------------------------- #


def life_sciences(revenue: LifeSciencesRevenue, timeline: Timeline) -> RevenueResult:
    """Contracted revenue only.

    A preclinical company has no product revenue, and inventing a commercial ramp
    inside the horizon would be the single most misleading thing this tool could do.
    Only a signed upfront the deck actually states appears here; royalties depend on
    an approval that has not happened, so they are left out of the operating model.
    """
    months = timeline.months
    recognised = np.zeros(months)

    upfront = revenue.partnership_upfront.value
    if upfront > 0 and months:
        # Recognised over the first year of the collaboration, not banked on day one.
        span = min(MONTHS_PER_YEAR, months)
        recognised[:span] = upfront / span

    grant = revenue.non_dilutive_funding.value
    grant_line = np.zeros(months)
    if grant > 0 and months:
        span = min(MONTHS_PER_YEAR, months)
        grant_line[:span] = grant / span

    return RevenueResult(
        recognised=recognised,
        billings=recognised.copy(),
        new_customers=np.zeros(months),
        ending_customers=np.zeros(months),
        components={"partnership": recognised, "grant funding": grant_line},
    )


def program_spend(revenue: LifeSciencesRevenue, timeline: Timeline) -> Vector:
    """R&D spend implied by the development programme, month by month.

    Each phase spends its total evenly across its duration. Phases that start beyond
    the horizon contribute nothing rather than being compressed into it.
    """
    spend = timeline.zeros()
    for phase in revenue.program_phases:
        start = round(phase.start_month_offset.value)
        duration = max(1, round(phase.duration_months.value))
        if start >= timeline.months:
            continue
        end = min(start + duration, timeline.months)
        spend[start:end] += phase.total_cost.value / duration
    return spend


# --------------------------------------------------------------------------- #
# Hardware -- razor / razor-blade
# --------------------------------------------------------------------------- #


def hardware(revenue: HardwareRevenue, timeline: Timeline) -> RevenueResult:
    """Device placements plus the consumable stream the installed base pulls through.

    Two things a naive device model gets wrong, both handled here: the consumable
    stream compounds off the *installed base* rather than current-period units, and
    revenue sold through a distributor is realised net of their margin. Omitting the
    first understates the business badly; omitting the second overstates it.
    """
    months = timeline.months
    units = revenue.units_month_1.value * decaying_growth_curve(
        months,
        revenue.unit_growth_monthly_pct.value,
        revenue.growth_decay_annual_pct.value,
        revenue.terminal_growth_monthly_pct.value,
    )

    installed_base = revenue.installed_base_start.value + np.cumsum(units)

    direct = revenue.direct_sales_mix_pct.value / 100.0
    distributor_haircut = revenue.distributor_margin_pct.value / 100.0
    realisation = direct + (1.0 - direct) * (1.0 - distributor_haircut)

    device = units * revenue.device_asp.value * realisation

    consumable_units = (
        installed_base * revenue.consumables_per_device_per_year.value / MONTHS_PER_YEAR
    )
    consumable = consumable_units * revenue.consumable_price.value * realisation

    recognised = device + consumable

    return RevenueResult(
        recognised=recognised,
        billings=recognised.copy(),
        new_customers=units,
        ending_customers=installed_base,
        # The consumable stream is the recurring part, and it is what a sales hire is
        # ultimately building. Device revenue is one-off per placement.
        new_recurring=consumable_units
        * revenue.consumable_price.value
        * realisation
        * MONTHS_PER_YEAR,
        components={"device": device, "consumables": consumable},
    )


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #


def build(assumptions: ModelAssumptions, timeline: Timeline) -> RevenueResult:
    """Run the engine matching this company's business model."""
    revenue = assumptions.revenue
    if isinstance(revenue, SaaSRevenue):
        return saas(revenue, timeline)
    if isinstance(revenue, LifeSciencesRevenue):
        return life_sciences(revenue, timeline)
    return hardware(revenue, timeline)
