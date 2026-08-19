"""The one-page investor summary.

Hard constraint: exactly one page, US Letter portrait, 0.5" margins, nothing clipped.
Content is laid out at a target size and, if it would overflow, shrinks adaptively --
optional rows drop and leading tightens -- rather than spilling onto a second page.
`tests/render/` asserts the page count.

The section a partner actually reads is *Key assumptions*, because that is where the
deal's load-bearing numbers are and where their provenance is visible. It gets the
most legible treatment on the page, not the least.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen.canvas import Canvas

from pitchdeck_cfo import MODEL_VERSION
from pitchdeck_cfo.assume.overrides import resolved_names
from pitchdeck_cfo.model.build import FinancialModel
from pitchdeck_cfo.model.headcount import FUNCTIONS
from pitchdeck_cfo.models import PROVENANCE_MARK, Provenance, Sourced
from pitchdeck_cfo.render import charts

PAGE_WIDTH, PAGE_HEIGHT = letter
MARGIN = 0.5 * inch
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN

INK = colors.HexColor("#111111")
MUTED = colors.HexColor("#666666")
FAINT = colors.HexColor("#CCCCCC")
NEGATIVE = colors.HexColor("#B03A00")
ACCENT = colors.HexColor("#0072B2")
TILE_BG = colors.HexColor("#F5F6F7")

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"

# Three sizes, and an 8pt floor that the adaptive shrink is not allowed to cross.
SIZE_TITLE = 15
SIZE_BODY = 8
SIZE_SMALL = 6.5
MIN_BODY = 8.0

LOGO = Path(__file__).resolve().parents[4] / "TEN_Capital_logo_footer.png"


@dataclass
class Cursor:
    """Vertical position, counted down the page."""

    y: float

    def down(self, amount: float) -> float:
        self.y -= amount
        return self.y


def _money(value: float, divisor: float, decimals: int | None = None) -> str:
    """Format at a fixed scale, negatives in parentheses.

    Decimals default to the scale: one at $M, none at $K. Whole millions read as
    "1" where the figure is $1.2M, which discards the precision the reader came for.
    """
    if decimals is None:
        decimals = 1 if divisor >= 1_000_000 else 0
    scaled = value / divisor
    text = f"{abs(scaled):,.{decimals}f}"
    return f"({text})" if value < 0 else text


def _standalone_money(value: float) -> str:
    """A single figure at whatever scale reads best for it.

    The page-wide scale is right for a column of comparable numbers and wrong for a
    lone assumption: an ACV of $64,800 renders as "0.06M" against a $M page.

    The sign goes outside the currency symbol. Formatting the signed value directly
    yields "$-6.4M", which is not how anyone writes money.
    """
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if magnitude >= 1_000_000:
        return f"{sign}${magnitude / 1_000_000:,.1f}M"
    if magnitude >= 10_000:
        return f"{sign}${magnitude / 1_000:,.0f}K"
    if magnitude >= 1_000:
        return f"{sign}${magnitude:,.0f}"
    return f"{sign}${magnitude:,.2f}".rstrip("0").rstrip(".")


def _scale_for(values: list[float]) -> tuple[float, str]:
    """Pick one magnitude for the whole page and state it once.

    The threshold is $10M, not $1M. A company whose revenue peaks at $6M renders as
    "0, 1, 1, 1, 2" in millions -- every figure rounded into uselessness. Holding it
    in thousands until the numbers are genuinely large keeps them readable.
    """
    peak = max((abs(v) for v in values), default=0.0)
    if peak >= 10_000_000:
        return 1_000_000.0, "$M"
    if peak >= 1_000:
        return 1_000.0, "$K"
    return 1.0, "$"


def _fit(canvas: Canvas, text: str, font: str, size: float, width: float) -> float:
    """Largest size at or below `size` at which `text` fits in `width`."""
    while size > 5.0 and canvas.stringWidth(text, font, size) > width:
        size -= 0.25
    return size


def _truncate(canvas: Canvas, text: str, font: str, size: float, width: float) -> str:
    if canvas.stringWidth(text, font, size) <= width:
        return text
    ellipsis = "…"
    while text and canvas.stringWidth(text + ellipsis, font, size) > width:
        text = text[:-1]
    return text.rstrip() + ellipsis


class OnePager:
    def __init__(self, model: FinancialModel) -> None:
        self.model = model
        self.assumptions = model.assumptions
        self.resolved = resolved_names(model.assumptions)
        self.divisor, self.unit = _scale_for(
            [*model.pnl_annual["Revenue"], *model.pnl_annual["Total Opex"]]
        )

    # ------------------------------------------------------------------ #

    def render(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        canvas = Canvas(str(path), pagesize=letter)
        canvas.setTitle(f"{self.assumptions.company.name} — financial model summary")

        cursor = Cursor(PAGE_HEIGHT - MARGIN)
        self._header(canvas, cursor)
        self._kpi_strip(canvas, cursor)
        self._summary_table(canvas, cursor)
        self._charts(canvas, cursor)
        self._assumptions(canvas, cursor)
        self._metrics(canvas, cursor)
        self._headcount(canvas, cursor)
        self._delta_note(canvas, cursor)
        self._footer(canvas)

        canvas.showPage()
        canvas.save()
        return path

    # ------------------------------------------------------------------ #

    def _header(self, canvas: Canvas, cursor: Cursor) -> None:
        company = self.assumptions.company
        y = cursor.down(SIZE_TITLE)

        logo_width = 0.0
        if LOGO.exists():
            logo_width = 0.9 * inch
            canvas.drawImage(
                str(LOGO),
                PAGE_WIDTH - MARGIN - logo_width,
                y - 2,
                width=logo_width,
                height=0.33 * inch,
                mask="auto",
                preserveAspectRatio=True,
                anchor="ne",
            )

        raise_amount = self.assumptions.financing.raise_amount.value
        ask = f"Raising {_standalone_money(raise_amount)}" if raise_amount else ""
        ask_width = canvas.stringWidth(ask, FONT_BOLD, SIZE_BODY) if ask else 0.0

        available = CONTENT_WIDTH - logo_width - ask_width - 24
        size = _fit(canvas, company.name, FONT_BOLD, SIZE_TITLE, available)
        canvas.setFont(FONT_BOLD, size)
        canvas.setFillColor(INK)
        canvas.drawString(MARGIN, y, company.name)

        if ask:
            canvas.setFont(FONT_BOLD, SIZE_BODY)
            canvas.setFillColor(ACCENT)
            canvas.drawRightString(PAGE_WIDTH - MARGIN - logo_width - 8, y + 2, ask)

        y = cursor.down(11)
        canvas.setFont(FONT, SIZE_SMALL)
        canvas.setFillColor(MUTED)
        chips = " · ".join(
            part
            for part in (
                company.business_model.replace("_", " "),
                company.sector,
                f"Financial model summary · {self.model.horizon_years} years from "
                f"{company.start_month}",
                f"Prepared {date.today():%d %b %Y}",
            )
            if part
        )
        canvas.drawString(MARGIN, y, _truncate(canvas, chips, FONT, SIZE_SMALL, CONTENT_WIDTH))

        y = cursor.down(6)
        canvas.setStrokeColor(FAINT)
        canvas.setLineWidth(0.6)
        canvas.line(MARGIN, y, PAGE_WIDTH - MARGIN, y)
        cursor.down(4)

    # ------------------------------------------------------------------ #

    def _kpi_tiles(self) -> list[tuple[str, str, str]]:
        model = self.model
        final = model.metrics[-1]
        tiles: list[tuple[str, str, str]] = [
            (
                f"Y{model.horizon_years} revenue",
                _standalone_money(model.final_year_revenue),
                "",
            ),
            (
                f"Y{model.horizon_years} EBITDA margin",
                f"{final.ebitda_margin_pct:.0f}%" if final.ebitda_margin_pct else "n/a",
                "",
            ),
            (
                "EBITDA break-even",
                model.ebitda_positive_month or "beyond horizon",
                "",
            ),
            (
                "Peak cash need",
                _standalone_money(model.peak_cash_need) if model.peak_cash_need else "none",
                "",
            ),
        ]
        ltv_cac = final.ltv_to_cac
        tiles.append(
            (
                "LTV / CAC" if ltv_cac else "Revenue per FTE",
                f"{ltv_cac:.1f}x"
                if ltv_cac
                else (_standalone_money(final.revenue_per_fte) if final.revenue_per_fte else "n/a"),
                PROVENANCE_MARK["derived"],
            )
        )
        return tiles

    def _kpi_strip(self, canvas: Canvas, cursor: Cursor) -> None:
        tiles = self._kpi_tiles()
        height = 0.52 * inch
        gap = 6
        width = (CONTENT_WIDTH - gap * (len(tiles) - 1)) / len(tiles)
        y = cursor.down(height)

        for i, (label, value, mark) in enumerate(tiles):
            x = MARGIN + i * (width + gap)
            canvas.setFillColor(TILE_BG)
            canvas.rect(x, y, width, height, stroke=0, fill=1)

            canvas.setFillColor(MUTED)
            canvas.setFont(FONT, SIZE_SMALL - 0.5)
            canvas.drawString(
                x + 5,
                y + height - 10,
                _truncate(canvas, label.upper(), FONT, SIZE_SMALL - 0.5, width - 10),
            )

            size = _fit(canvas, value, FONT_BOLD, 12.5, width - 10)
            canvas.setFillColor(INK)
            canvas.setFont(FONT_BOLD, size)
            canvas.drawString(x + 5, y + 7, value)
            if mark:
                canvas.setFont(FONT, SIZE_SMALL - 1)
                canvas.setFillColor(MUTED)
                canvas.drawString(
                    x + 6 + canvas.stringWidth(value, FONT_BOLD, size), y + 7 + size * 0.55, mark
                )

        cursor.down(9)

    # ------------------------------------------------------------------ #

    SUMMARY_ROWS: tuple[tuple[str, str, str], ...] = (
        ("Revenue", "Revenue", "money"),
        ("YoY growth", "", "growth"),
        ("COGS", "COGS", "money"),
        ("Gross profit", "Gross Profit", "money"),
        ("Gross margin", "Gross Margin %", "pct"),
        ("R&D", "R&D", "money"),
        ("S&M", "S&M", "money"),
        ("G&A", "G&A", "money"),
        ("Total opex", "Total Opex", "money"),
        ("EBITDA", "EBITDA", "money"),
        ("EBITDA margin", "EBITDA Margin %", "pct"),
        ("Ending headcount", "", "headcount"),
        ("Ending cash", "", "cash"),
    )

    def _summary_table(self, canvas: Canvas, cursor: Cursor) -> None:
        model = self.model
        years = model.year_labels
        label_width = 1.15 * inch
        column = (CONTENT_WIDTH - label_width) / len(years)
        line_height = 11.2

        y = cursor.down(11)
        canvas.setFont(FONT_BOLD, SIZE_SMALL)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, y, f"FINANCIAL SUMMARY · {self.unit}")
        for i, label in enumerate(years):
            canvas.drawRightString(MARGIN + label_width + column * (i + 1) - 2, y, label.upper())
        y = cursor.down(3)
        canvas.setStrokeColor(FAINT)
        canvas.line(MARGIN, y, PAGE_WIDTH - MARGIN, y)

        for label, key, kind in self.SUMMARY_ROWS:
            y = cursor.down(line_height)
            emphasis = label in ("Revenue", "EBITDA", "Gross profit")
            canvas.setFont(FONT_BOLD if emphasis else FONT, SIZE_BODY)
            canvas.setFillColor(INK)
            canvas.drawString(MARGIN, y, label)

            for i in range(len(years)):
                text, negative = self._summary_cell(kind, key, i)
                canvas.setFillColor(NEGATIVE if negative else INK)
                canvas.setFont(FONT_BOLD if emphasis else FONT, SIZE_BODY)
                canvas.drawRightString(MARGIN + label_width + column * (i + 1) - 2, y, text)

            if label in ("Gross margin", "EBITDA margin"):
                canvas.setStrokeColor(FAINT)
                canvas.setLineWidth(0.4)
                canvas.line(MARGIN, y - 3, PAGE_WIDTH - MARGIN, y - 3)

        cursor.down(8)

    def _summary_cell(self, kind: str, key: str, index: int) -> tuple[str, bool]:
        model = self.model
        if kind == "money":
            value = model.pnl_annual[key][index]
            return _money(value, self.divisor, 0), value < 0
        if kind == "pct":
            value = model.pnl_annual[key][index]
            return f"{value:.1f}%", value < 0
        if kind == "growth":
            growth = model.metrics[index].revenue_growth_pct
            return ("—", False) if growth is None else (f"{growth:.0f}%", growth < 0)
        if kind == "headcount":
            return f"{model.headcount_annual['Total'][index]:,.0f}", False
        value = model.ending_cash[index]
        return _money(value, self.divisor, 0), value < 0

    # ------------------------------------------------------------------ #

    def _charts(self, canvas: Canvas, cursor: Cursor) -> None:
        height = 1.85 * inch
        gap = 12
        width = (CONTENT_WIDTH - gap) / 2
        y = cursor.down(height)

        for i, png in enumerate(
            (charts.revenue_vs_cost(self.model), charts.cash_balance(self.model))
        ):
            from reportlab.lib.utils import ImageReader

            canvas.drawImage(
                ImageReader(io.BytesIO(png)),
                MARGIN + i * (width + gap),
                y,
                width=width,
                height=height,
                preserveAspectRatio=True,
                anchor="sw",
                mask="auto",
            )
        cursor.down(7)

    # ------------------------------------------------------------------ #

    # The load-bearing drivers, in the order a reader cares about them. Anything the
    # model does not carry is skipped rather than shown blank.
    ASSUMPTION_ORDER: tuple[tuple[str, str, str], ...] = (
        ("revenue.starting_arr", "Starting ARR", "money"),
        ("revenue.arpu_annual", "ARPU / ACV", "money"),
        ("revenue.new_logo_growth_monthly_pct", "New-logo growth", "pct_month"),
        ("revenue.net_revenue_retention_pct", "Net revenue retention", "pct"),
        ("revenue.logo_churn_annual_pct", "Logo churn", "pct"),
        ("revenue.device_asp", "Device ASP", "money"),
        ("revenue.consumable_price", "Consumable price", "money"),
        ("revenue.units_month_1", "Units placed, month 1", "plain"),
        ("revenue.unit_growth_monthly_pct", "Unit growth", "pct_month"),
        ("revenue.consumables_per_device_per_year", "Consumable attach rate", "plain"),
        ("revenue.growth_decay_annual_pct", "Growth decay", "pct"),
        ("cogs.target_gross_margin_pct", "Gross margin", "pct"),
        ("cogs.hosting_scale_exponent", "Infra scale exponent", "plain"),
        ("headcount.loaded_multiplier", "Loaded cost multiplier", "plain"),
        ("headcount.rep_quota_annual", "Rep quota", "money"),
        ("headcount.scale_exponent", "Headcount scale exponent", "plain"),
        ("opex.marketing_pct_of_new_revenue", "Marketing % of new revenue", "pct"),
        ("financing.cash_on_hand", "Cash on hand", "money"),
        ("financing.raise_amount", "Raise", "money"),
        ("tax.blended_rate_pct", "Blended tax rate", "pct"),
    )
    MAX_ASSUMPTIONS = 12

    def _assumption_lines(self) -> list[tuple[str, str, Provenance]]:
        lines: list[tuple[str, str, Provenance]] = []
        for path, label, kind in self.ASSUMPTION_ORDER:
            value = self.resolved.get(path)
            if value is None or not isinstance(value.value, int | float):
                continue
            if value.value == 0 and kind in ("money", "plain"):
                continue
            lines.append((label, self._format_assumption(value, kind), value.source))
            if len(lines) == self.MAX_ASSUMPTIONS:
                break
        return lines

    def _format_assumption(self, value: Sourced[float], kind: str) -> str:
        number = float(value.value)
        if kind == "money":
            return _standalone_money(number)
        if kind == "pct":
            return f"{number:.1f}%"
        if kind == "pct_month":
            return f"{number:.1f}%/mo"
        return f"{number:,.2f}".rstrip("0").rstrip(".")

    def _assumptions(self, canvas: Canvas, cursor: Cursor) -> None:
        lines = self._assumption_lines()
        y = cursor.down(11)
        canvas.setFont(FONT_BOLD, SIZE_SMALL)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, y, "KEY ASSUMPTIONS")
        y = cursor.down(3)
        canvas.setStrokeColor(FAINT)
        canvas.line(MARGIN, y, PAGE_WIDTH - MARGIN, y)

        column_width = CONTENT_WIDTH / 2
        rows = (len(lines) + 1) // 2
        line_height = 11.0

        start = cursor.y
        for i, (label, value, source) in enumerate(lines):
            column, offset = divmod(i, rows)
            row_y = start - (offset + 1) * line_height
            x = MARGIN + column * column_width

            canvas.setFont(FONT, SIZE_BODY)
            canvas.setFillColor(INK)
            canvas.drawString(
                x, row_y, _truncate(canvas, label, FONT, SIZE_BODY, column_width * 0.52)
            )

            mark = PROVENANCE_MARK[source]
            canvas.setFont(FONT_BOLD, SIZE_BODY)
            value_x = x + column_width - 12
            canvas.drawRightString(value_x, row_y, value)

            canvas.setFont(FONT, SIZE_SMALL - 0.5)
            canvas.setFillColor(MUTED)
            canvas.drawString(value_x + 2, row_y + 2, mark or "")

        cursor.down(rows * line_height + 7)

    # ------------------------------------------------------------------ #

    METRIC_ROWS: tuple[tuple[str, str, str], ...] = (
        ("CAC", "cac", "money"),
        ("CAC payback", "cac_payback_months", "months"),
        ("LTV / CAC", "ltv_to_cac", "multiple"),
        ("Magic number", "magic_number", "ratio"),
        ("Burn multiple", "burn_multiple", "ratio"),
        ("Rule of 40", "rule_of_40", "points"),
        ("Net revenue retention", "net_revenue_retention_pct", "pct"),
        ("Revenue per FTE", "revenue_per_fte", "money"),
    )

    def _metrics(self, canvas: Canvas, cursor: Cursor) -> None:
        """Unit economics, computed from the model rather than restated from the deck.

        A metric that is undefined prints an em dash. Showing 0 for a CAC when no
        customers were won would read as free acquisition.
        """
        rows = [
            (label, key, kind)
            for label, key, kind in self.METRIC_ROWS
            if any(getattr(m, key) is not None for m in self.model.metrics)
        ]
        if not rows:
            return

        y = cursor.down(11)
        canvas.setFont(FONT_BOLD, SIZE_SMALL)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, y, "UNIT ECONOMICS & EFFICIENCY")
        label_width = 1.35 * inch
        column = (CONTENT_WIDTH - label_width) / len(self.model.year_labels)
        for i, label in enumerate(self.model.year_labels):
            canvas.drawRightString(MARGIN + label_width + column * (i + 1) - 2, y, label.upper())
        y = cursor.down(3)
        canvas.setStrokeColor(FAINT)
        canvas.line(MARGIN, y, PAGE_WIDTH - MARGIN, y)

        for label, key, kind in rows:
            y = cursor.down(10.2)
            canvas.setFont(FONT, SIZE_BODY)
            canvas.setFillColor(INK)
            canvas.drawString(MARGIN, y, label)
            for i, metric in enumerate(self.model.metrics):
                value = getattr(metric, key)
                canvas.setFillColor(INK if value is None or value >= 0 else NEGATIVE)
                canvas.drawRightString(
                    MARGIN + label_width + column * (i + 1) - 2,
                    y,
                    self._format_metric(value, kind),
                )
        cursor.down(7)

    @staticmethod
    def _format_metric(value: float | None, kind: str) -> str:
        if value is None:
            return "—"
        if kind == "money":
            return _standalone_money(value)
        if kind == "months":
            return f"{value:,.0f} mo"
        if kind == "multiple":
            return f"{value:,.1f}x"
        if kind == "pct":
            return f"{value:,.0f}%"
        if kind == "points":
            return f"{value:,.0f}"
        return f"{value:,.2f}"

    def _headcount(self, canvas: Canvas, cursor: Cursor) -> None:
        model = self.model
        active = [f for f in FUNCTIONS if any(v > 0 for v in model.headcount_annual.get(f, ()))]
        if not active:
            return

        y = cursor.down(11)
        canvas.setFont(FONT_BOLD, SIZE_SMALL)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, y, "HEADCOUNT PLAN")

        label_width = 1.15 * inch
        column = (CONTENT_WIDTH - label_width) / len(model.year_labels)
        for i, label in enumerate(model.year_labels):
            canvas.drawRightString(MARGIN + label_width + column * (i + 1) - 2, y, label.upper())
        y = cursor.down(3)
        canvas.setStrokeColor(FAINT)
        canvas.line(MARGIN, y, PAGE_WIDTH - MARGIN, y)

        # Only functions the plan actually staffs, so the block stays compact.
        for function in active:
            y = cursor.down(10.2)
            canvas.setFont(FONT, SIZE_BODY)
            canvas.setFillColor(INK)
            canvas.drawString(MARGIN, y, function)
            for i, value in enumerate(model.headcount_annual[function]):
                canvas.drawRightString(
                    MARGIN + label_width + column * (i + 1) - 2, y, f"{value:,.0f}"
                )

        y = cursor.down(10.2)
        canvas.setFont(FONT_BOLD, SIZE_BODY)
        canvas.drawString(MARGIN, y, "Total FTE")
        for i, value in enumerate(model.headcount_annual["Total"]):
            canvas.drawRightString(MARGIN + label_width + column * (i + 1) - 2, y, f"{value:,.0f}")

        y = cursor.down(10.2)
        canvas.setFont(FONT, SIZE_BODY)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, y, "Loaded cost")
        for i, value in enumerate(model.headcount_cost_annual["Total"]):
            canvas.drawRightString(
                MARGIN + label_width + column * (i + 1) - 2, y, _money(value, self.divisor, 1)
            )
        cursor.down(6)

    # ------------------------------------------------------------------ #

    def _delta_note(self, canvas: Canvas, cursor: Cursor) -> None:
        """Where the deck's own forecast and this model disagree, say so.

        A partner reading a $1.6M model of a company whose deck promises $144M needs
        that gap stated on the page, not discovered later.
        """
        claim = self._deck_claim()
        if claim is None:
            return
        label, deck_value, model_value = claim
        if model_value <= 0:
            return
        ratio = deck_value / model_value
        if 0.6 <= ratio <= 1.6:
            return

        direction = "above" if ratio > 1 else "below"
        headline = (
            f"DECK VS MODEL — the company's own {label} projection of "
            f"{_standalone_money(deck_value)} is {ratio:,.0f}x {direction} this "
            f"model's {_standalone_money(model_value)}."
        )
        detail = (
            "The model uses labelled benchmarks wherever the deck stated no driver. "
            "The gap is the question to put to the founder."
        )

        y = cursor.down(11)
        canvas.setFont(FONT_BOLD, SIZE_BODY)
        canvas.setFillColor(NEGATIVE)
        canvas.drawString(
            MARGIN, y, _truncate(canvas, headline, FONT_BOLD, SIZE_BODY, CONTENT_WIDTH)
        )
        y = cursor.down(9)
        canvas.setFont(FONT, SIZE_SMALL)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, y, _truncate(canvas, detail, FONT, SIZE_SMALL, CONTENT_WIDTH))
        cursor.down(3)

    def _deck_claim(self) -> tuple[str, float, float] | None:
        """The deck's own peak revenue projection, if it stated one."""
        projection = self.model.deck_final_year_revenue
        if projection is None:
            return None
        return "peak revenue", float(projection), self.model.final_year_revenue

    # ------------------------------------------------------------------ #

    def _footer(self, canvas: Canvas) -> None:
        coverage = self.assumptions.coverage
        y = MARGIN - 2
        canvas.setStrokeColor(FAINT)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN, y + 22, PAGE_WIDTH - MARGIN, y + 22)

        canvas.setFont(FONT, SIZE_SMALL - 0.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, y + 14, coverage.summary_line())

        legend = (
            f"{PROVENANCE_MARK['derived']} derived   "
            f"{PROVENANCE_MARK['benchmark']} benchmark   "
            f"{PROVENANCE_MARK['user']} your override   "
            f"unmarked = stated in the deck"
        )
        canvas.drawString(MARGIN, y + 7, legend)

        disclaimer = (
            f"pitchdeck-cfo {MODEL_VERSION} · A model built from figures stated in an "
            f"investor deck and filled out with labelled benchmarks. Not audited "
            f"financial information, and not a forecast the company has endorsed."
        )
        canvas.drawString(
            MARGIN, y, _truncate(canvas, disclaimer, FONT, SIZE_SMALL - 0.5, CONTENT_WIDTH)
        )


def write(model: FinancialModel, path: Path) -> Path:
    """Render the one-page summary."""
    return OnePager(model).render(path)
