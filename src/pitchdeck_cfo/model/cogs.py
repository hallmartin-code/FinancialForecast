"""Cost of revenue, and the one mechanism that produces margin expansion honestly.

Most models assert that gross margin improves from 65% to 80% over five years and
leave it there. Here margin expansion is an *output*: infrastructure cost scales
sub-linearly with revenue (`revenue^exponent`, exponent below 1.0), so the margin
widens because the cost curve says so. Change the exponent to 1.0 in the assumptions
and the expansion disappears, which is the correct behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pitchdeck_cfo.assume.schema import ModelAssumptions
from pitchdeck_cfo.model.revenue import RevenueResult
from pitchdeck_cfo.model.timeline import Timeline, Vector


@dataclass(frozen=True)
class COGSResult:
    total: Vector
    components: dict[str, Vector]


def _reference_revenue(revenue: Vector) -> float:
    """The revenue level at which the stated cost percentage holds exactly.

    The first month with any revenue in it. Anchoring to month zero would break for a
    pre-revenue company, and anchoring to the mean would make the stated percentage
    true at no point in the model.
    """
    non_zero = revenue[revenue > 0]
    return float(non_zero[0]) if non_zero.size else 0.0


def scaled_cost(revenue: Vector, pct_of_revenue: float, exponent: float) -> Vector:
    """A cost that is `pct` of revenue at the reference level and scales sub-linearly.

    At the reference revenue this returns exactly `pct` of it. Above that it grows
    with `revenue^exponent`, so cost as a share of revenue falls.
    """
    reference = _reference_revenue(revenue)
    if reference <= 0 or pct_of_revenue <= 0:
        return np.zeros_like(revenue)
    ratio = np.divide(revenue, reference, out=np.zeros_like(revenue), where=revenue > 0)
    return (pct_of_revenue / 100.0) * reference * np.power(ratio, exponent)


def build(
    assumptions: ModelAssumptions,
    revenue: RevenueResult,
    timeline: Timeline,
) -> COGSResult:
    """Cost of revenue by component, shaped by the business model."""
    cogs = assumptions.cogs
    total = timeline.zeros()
    components: dict[str, Vector] = {}
    recognised = revenue.recognised

    hosting = scaled_cost(
        recognised,
        cogs.hosting_pct_of_revenue.value,
        cogs.hosting_scale_exponent.value,
    )
    if hosting.any():
        components["hosting & infrastructure"] = hosting
        total = total + hosting

    support = recognised * cogs.support_pct_of_revenue.value / 100.0
    if support.any():
        components["support & success"] = support
        total = total + support

    processing = recognised * cogs.payment_processing_pct.value / 100.0
    if processing.any():
        components["payment processing"] = processing
        total = total + processing

    # Hardware: the device and the consumable carry different margins, so they are
    # costed separately rather than through one blended rate.
    device_revenue = revenue.components.get("device")
    if device_revenue is not None and cogs.bom_pct_of_asp.value > 0:
        bom = device_revenue * cogs.bom_pct_of_asp.value / 100.0
        components["bill of materials"] = bom
        total = total + bom

    consumable_revenue = revenue.components.get("consumables")
    if consumable_revenue is not None and cogs.consumable_gross_margin_pct.value > 0:
        consumable_cost = consumable_revenue * (
            1.0 - cogs.consumable_gross_margin_pct.value / 100.0
        )
        components["consumable cost"] = consumable_cost
        total = total + consumable_cost

    warranty = recognised * cogs.warranty_pct_of_revenue.value / 100.0
    if warranty.any():
        components["warranty & field service"] = warranty
        total = total + warranty

    return COGSResult(total=total, components=components)


def gross_profit(revenue: Vector, cogs: Vector) -> Vector:
    return revenue - cogs


def gross_margin_pct(revenue: Vector, gross: Vector) -> Vector:
    """Margin where there is revenue, zero where there is not.

    A pre-revenue month has no gross margin. Reporting one -- or a divide-by-zero
    infinity -- would be worse than reporting nothing.
    """
    return np.divide(gross, revenue, out=np.zeros_like(revenue), where=revenue > 0) * 100.0
