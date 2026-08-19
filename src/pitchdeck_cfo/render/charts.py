"""The two charts, rendered to PNG in memory.

Both are drawn from the same `FinancialModel` the tables come from, so a chart cannot
tell a different story than the numbers beside it.

Colours are from a colourblind-safe palette and are distinguishable in greyscale,
because a one-pager gets printed. No gridline clutter, no legends where a direct
label will do, no chart junk.
"""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter

from pitchdeck_cfo.model.build import FinancialModel

DPI = 200

# Okabe-Ito, which stays legible for the common colour-vision deficiencies and
# separates by lightness so it survives a greyscale printer.
COST = "#E69F00"
REVENUE = "#0072B2"
PROFIT = "#009E73"
LOSS = "#D55E00"
RULE = "#666666"
FAINT = "#BBBBBB"

plt.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": ["Helvetica", "Arial", "DejaVu Sans"],
        "font.size": 7,
        "axes.edgecolor": RULE,
        "axes.linewidth": 0.6,
        "axes.labelcolor": "#222222",
        "text.color": "#222222",
        "xtick.color": RULE,
        "ytick.color": RULE,
        "xtick.major.width": 0.5,
        "ytick.major.width": 0.5,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
    }
)


def _scale(values: list[float]) -> tuple[float, str]:
    """One magnitude for the whole chart, matching the one-pager's own threshold."""
    peak = max((abs(v) for v in values), default=0.0)
    if peak >= 10_000_000:
        return 1_000_000.0, "$M"
    if peak >= 1_000:
        return 1_000.0, "$K"
    return 1.0, "$"


def _compact(value: float) -> str:
    """A single annotated figure at its own natural scale.

    The chart's axis scale is right for the axis and wrong for a callout: on a $K
    axis, the raise annotates as "2,000.0K" where "$2.0M" is what a reader wants.
    """
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if magnitude >= 1_000_000:
        return f"{sign}${magnitude / 1_000_000:,.1f}M"
    if magnitude >= 1_000:
        return f"{sign}${magnitude / 1_000:,.0f}K"
    return f"{sign}${magnitude:,.0f}"


def _tick_formatter(axis_range: float) -> FuncFormatter:
    """Enough decimals that no two ticks print the same label.

    A fixed format produced axes reading "0, -0, -0, -1, -1, -2, -2" on a small
    range, which is worse than no axis at all.
    """
    decimals = 0 if axis_range >= 8 else (1 if axis_range >= 1 else 2)
    return FuncFormatter(lambda v, _: f"{v:,.{decimals}f}")


def _strip(ax: Axes) -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=2, pad=1.5)


def _to_png(figure: Figure) -> bytes:
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=DPI, bbox_inches="tight", pad_inches=0.02)
    plt.close(figure)
    return buffer.getvalue()


def revenue_vs_cost(model: FinancialModel, width: float = 3.5, height: float = 2.15) -> bytes:
    """Stacked cost against revenue, with EBITDA on a secondary axis.

    The question this answers is when cost stops outrunning revenue, so the two are
    drawn on the same axis and EBITDA rides above with its own scale and a zero rule.
    """
    revenue = list(model.pnl_annual["Revenue"])
    cogs = list(model.pnl_annual["COGS"])
    opex = list(model.pnl_annual["Total Opex"])
    ebitda = list(model.pnl_annual["EBITDA"])
    years = list(model.year_labels)

    divisor, unit = _scale(revenue + [c + o for c, o in zip(cogs, opex, strict=True)])
    figure, ax = plt.subplots(figsize=(width, height))

    positions = range(len(years))
    bar_width = 0.38
    left = [p - bar_width / 2 for p in positions]
    right = [p + bar_width / 2 for p in positions]

    ax.bar(left, [v / divisor for v in revenue], bar_width, color=REVENUE, label="Revenue")
    ax.bar(right, [v / divisor for v in cogs], bar_width, color=COST, label="COGS")
    ax.bar(
        right,
        [v / divisor for v in opex],
        bar_width,
        bottom=[v / divisor for v in cogs],
        color=COST,
        alpha=0.55,
        label="Opex",
    )

    ax.set_xticks(list(positions))
    ax.set_xticklabels(years)
    ax.set_ylabel(unit, labelpad=1)
    ax.yaxis.set_major_formatter(_tick_formatter(max(v / divisor for v in revenue)))
    _strip(ax)

    twin = ax.twinx()
    twin.plot(
        list(positions),
        [v / divisor for v in ebitda],
        color=PROFIT,
        linewidth=1.4,
        marker="o",
        markersize=2.5,
        zorder=5,
    )
    twin.axhline(0, color=RULE, linewidth=0.5, linestyle=(0, (2, 2)))
    twin.set_ylabel(f"EBITDA {unit}", color=PROFIT, labelpad=1)
    twin.tick_params(axis="y", colors=PROFIT, length=2, pad=1.5)
    twin.yaxis.set_major_formatter(_tick_formatter((max(ebitda) - min(ebitda)) / divisor))
    for side in ("top", "left"):
        twin.spines[side].set_visible(False)

    # Direct labels were tried and collided in the first-year corner, where all three
    # series are small and close together. A single-row legend above the plot is
    # legible where three overlapping labels are not.
    handles = [
        Patch(facecolor=REVENUE, label="Revenue"),
        Patch(facecolor=COST, label="COGS + Opex"),
        Line2D([], [], color=PROFIT, linewidth=1.4, label="EBITDA"),
    ]
    ax.legend(
        handles=handles,
        loc="lower center",
        bbox_to_anchor=(0.5, 1.0),
        ncol=3,
        frameon=False,
        fontsize=6,
        handlelength=1.2,
        columnspacing=1.2,
        borderpad=0.0,
        handletextpad=0.4,
    )

    return _to_png(figure)


def cash_balance(model: FinancialModel, width: float = 3.5, height: float = 2.15) -> bytes:
    """Monthly cash, with zero marked and the raise annotated.

    Monthly rather than annual on purpose: a company that troughs in month 7 and
    recovers by month 12 looks solvent in an annual bar and is not.
    """
    cash = list(model.cash_monthly["Ending Cash"])
    financing = list(model.cash_monthly["Financing"])
    months = range(len(cash))

    divisor, unit = _scale(cash)
    figure, ax = plt.subplots(figsize=(width, height))

    scaled = [v / divisor for v in cash]
    ax.plot(list(months), scaled, color=REVENUE, linewidth=1.3)
    ax.fill_between(
        list(months),
        scaled,
        0,
        where=[v >= 0 for v in scaled],
        color=REVENUE,
        alpha=0.12,
        interpolate=True,
    )
    ax.fill_between(
        list(months),
        scaled,
        0,
        where=[v < 0 for v in scaled],
        color=LOSS,
        alpha=0.18,
        interpolate=True,
    )
    ax.axhline(0, color=RULE, linewidth=0.7)

    raise_month = next((i for i, v in enumerate(financing) if v > 0), None)
    if raise_month is not None:
        ax.axvline(raise_month, color=FAINT, linewidth=0.6, linestyle=(0, (2, 2)))
        ax.annotate(
            f"raise {_compact(financing[raise_month])}",
            xy=(raise_month, scaled[raise_month]),
            xytext=(4, 6),
            textcoords="offset points",
            fontsize=6,
            color=RULE,
        )

    trough = min(range(len(scaled)), key=lambda i: scaled[i])
    if scaled[trough] < 0:
        ax.annotate(
            f"low {_compact(cash[trough])}",
            xy=(trough, scaled[trough]),
            xytext=(3, -9),
            textcoords="offset points",
            fontsize=6,
            color=LOSS,
        )

    # One tick a year keeps the axis readable at this size.
    ticks = list(range(0, len(cash), 12))
    ax.set_xticks(ticks)
    ax.set_xticklabels([model.year_labels[i // 12] for i in ticks])
    ax.set_ylabel(unit, labelpad=1)
    ax.yaxis.set_major_formatter(_tick_formatter(max(scaled) - min(scaled)))
    ax.set_xlim(0, len(cash) - 1)
    _strip(ax)

    return _to_png(figure)
