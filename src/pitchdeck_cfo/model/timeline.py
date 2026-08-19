"""The model's calendar, and the rate conversions everything else depends on.

The engine works monthly and aggregates to fiscal years. That is not incidental
precision: break-even, peak cash need and runway are month-level facts, and a model
built on annual buckets cannot state them.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

MONTHS_PER_YEAR = 12

Vector = NDArray[np.float64]


@dataclass(frozen=True)
class Timeline:
    """`months` consecutive months starting at `start_month` (YYYY-MM)."""

    start_month: str
    horizon_years: int

    @property
    def months(self) -> int:
        return self.horizon_years * MONTHS_PER_YEAR

    @property
    def start(self) -> tuple[int, int]:
        year, month = self.start_month.split("-")
        return int(year), int(month)

    def month_labels(self) -> tuple[str, ...]:
        year, month = self.start
        labels: list[str] = []
        for _ in range(self.months):
            labels.append(f"{year:04d}-{month:02d}")
            month += 1
            if month > MONTHS_PER_YEAR:
                year, month = year + 1, 1
        return tuple(labels)

    def year_labels(self) -> tuple[str, ...]:
        return tuple(f"Y{i + 1}" for i in range(self.horizon_years))

    def year_of_month(self) -> NDArray[np.int64]:
        """Which fiscal year each month belongs to, zero-based."""
        return np.arange(self.months, dtype=np.int64) // MONTHS_PER_YEAR

    def zeros(self) -> Vector:
        return np.zeros(self.months, dtype=np.float64)

    def to_annual(self, monthly: Vector) -> Vector:
        """Sum a flow line into fiscal years."""
        return monthly.reshape(self.horizon_years, MONTHS_PER_YEAR).sum(axis=1)

    def end_of_year(self, monthly: Vector) -> Vector:
        """Take the closing value of a balance line for each fiscal year.

        Flows sum; balances do not. Summing a cash balance across twelve months
        produces a number with no meaning, so the two have separate helpers and the
        P&L builder is explicit about which each line is.
        """
        return monthly.reshape(self.horizon_years, MONTHS_PER_YEAR)[:, -1]

    def average_of_year(self, monthly: Vector) -> Vector:
        return monthly.reshape(self.horizon_years, MONTHS_PER_YEAR).mean(axis=1)


def annual_to_monthly_rate(annual_pct: float) -> float:
    """Compound an annual rate down to a monthly one.

    Dividing by twelve is wrong and the error compounds: 118% a year is 6.7% a month,
    not 9.8%, and over five years the difference is more than an order of magnitude.
    """
    return float((1.0 + annual_pct / 100.0) ** (1.0 / MONTHS_PER_YEAR) - 1.0)


def growth_curve(months: int, monthly_growth_pct: float) -> Vector:
    """Compounding multiplier for each month, starting at 1.0."""
    return np.power(1.0 + monthly_growth_pct / 100.0, np.arange(months, dtype=np.float64))


def first_index_where(values: Vector, predicate: NDArray[np.bool_]) -> int | None:
    """Index of the first month satisfying a condition, or None if it never does.

    Returning None rather than -1 or the horizon end forces callers to say "never
    inside the horizon" out loud instead of quietly reporting the last month.
    """
    hits = np.flatnonzero(predicate)
    return int(hits[0]) if hits.size else None


def decaying_growth_curve(
    months: int,
    monthly_growth_pct: float,
    decay_annual_pct: float,
    floor_monthly_pct: float,
) -> Vector:
    """A compounding multiplier whose growth rate decays toward a floor.

    No company holds its current growth rate for five years, and a model that assumes
    it does produces a number nobody believes. The rate decays by `decay_annual_pct`
    of itself each year and never falls below `floor_monthly_pct`.

    With `decay_annual_pct` at zero this reduces exactly to `growth_curve`, so a user
    who wants the deck's rate extrapolated flat can still have it.
    """
    if months <= 0:
        return np.zeros(0, dtype=np.float64)

    decay_per_month = (1.0 - decay_annual_pct / 100.0) ** (1.0 / MONTHS_PER_YEAR)
    rates = np.maximum(
        monthly_growth_pct * np.power(decay_per_month, np.arange(months, dtype=np.float64)),
        floor_monthly_pct,
    )
    # The curve starts at 1.0; month t compounds every rate before it.
    return np.concatenate(([1.0], np.cumprod(1.0 + rates[:-1] / 100.0)))
