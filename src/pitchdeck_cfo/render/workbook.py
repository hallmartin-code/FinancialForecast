"""The financial model workbook.

Seven sheets, annual columns, formula-driven throughout, following the structure in
`WORKBOOK_FORMAT.md`. The requirement that makes it worth anything is unchanged:
**every downstream cell is a live formula referencing the Assumptions tab**, so
changing an input recalculates the model in Excel with no Python involved.

The colour system is load-bearing rather than decorative -- blue is an input you may
change, green is a link to another sheet, black is computed here, and a yellow fill
marks an input this tool supplied because the company did not. That last one is the
whole provenance argument, rendered in a form a CFO already knows how to read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from pitchdeck_cfo import MODEL_VERSION
from pitchdeck_cfo.model.build import FinancialModel
from pitchdeck_cfo.model.opex import GA_FUNCTIONS, RD_FUNCTIONS, SM_FUNCTIONS
from pitchdeck_cfo.render import style as S
from pitchdeck_cfo.render.plan import MINIMUM_CASH, AnnualPlan, Line
from pitchdeck_cfo.render.plan import build as build_plan
from pitchdeck_cfo.render.sources import source_rows

SHEETS = (
    "Summary",
    "Assumptions",
    "Revenue Build",
    "Headcount",
    "P&L",
    "Cash Flow",
    "Sources & Notes",
)

NUMBER_FORMATS = {
    "money": S.MONEY,
    "count": S.COUNT,
    "percent": S.PERCENT,
    "ratio": S.RATIO,
}

# Written precision, by kind. Values are rounded so a cell does not carry a float
# tail no one wants to see, but a rate is a fraction: six decimals on a cost ratio is
# a rounding of one part in ten thousand, which on an eight-figure revenue line is
# dollars of drift against the engine. Rates therefore keep more.
PRECISION = {"money": 6, "count": 6, "percent": 12, "ratio": 12}


def _ref(sheet: str, column: int, row: int) -> str:
    """A cross-sheet reference, quoted where the sheet name needs it."""
    cell = f"{get_column_letter(column)}{row}"
    return f"'{sheet}'!{cell}" if (" " in sheet or "&" in sheet) else f"{sheet}!{cell}"


class WorkbookWriter:
    def __init__(self, model: FinancialModel) -> None:
        self.model = model
        self.plan: AnnualPlan = build_plan(model)
        self.years = len(self.plan.years)
        self.wb = Workbook()
        self.arow: dict[str, int] = {}
        self.rrow: dict[str, int] = {}
        self.hrow: dict[str, int] = {}
        self.prow: dict[str, int] = {}
        self.crow: dict[str, int] = {}

    # --- primitives -------------------------------------------------------- #

    def _col(self, year: int) -> int:
        return S.FIRST_YEAR_COL + year

    def _letter(self, year: int) -> str:
        return get_column_letter(self._col(year))

    def _last_col(self) -> int:
        return S.FIRST_YEAR_COL + self.years

    def _chrome(
        self, ws: Worksheet, title: str, note: str, label_width: int = S.LABEL_WIDTH
    ) -> int:
        """Title band, units note, year header. Identical on every sheet."""
        ws.merge_cells(f"A{S.TITLE_ROW}:{get_column_letter(self._last_col())}{S.TITLE_ROW}")
        cell = ws.cell(row=S.TITLE_ROW, column=1, value=title)
        cell.font = S.TITLE_FONT
        cell.fill = S.TITLE_FILL

        ws.cell(row=S.NOTE_ROW, column=1, value=note).font = S.NOTE_FONT

        for i, year in enumerate(self.plan.years):
            header = ws.cell(row=S.YEAR_ROW, column=self._col(i), value=int(year))
            header.font = S.YEAR_FONT
            header.fill = S.YEAR_FILL
            header.alignment = S.CENTRE

        ws.column_dimensions["A"].width = label_width
        for i in range(self.years + 1):
            ws.column_dimensions[get_column_letter(self._col(i))].width = S.YEAR_WIDTH
        ws.freeze_panes = f"B{S.YEAR_ROW + 1}"
        return S.YEAR_ROW + 2

    def _section(self, ws: Worksheet, row: int, title: str) -> int:
        ws.merge_cells(f"A{row}:{get_column_letter(self._last_col())}{row}")
        cell = ws.cell(row=row, column=1, value=title)
        cell.font = S.SECTION_FONT
        cell.fill = S.SECTION_FILL
        return row + 1

    def _row(
        self,
        ws: Worksheet,
        row: int,
        label: str,
        cell_for: Any,
        *,
        number_format: str = S.MONEY,
        font: Any = None,
        bold_label: bool = False,
    ) -> int:
        ws.cell(row=row, column=1, value=label).font = S.BOLD_LABEL if bold_label else S.LABEL_FONT
        for i in range(self.years):
            cell = ws.cell(row=row, column=self._col(i), value=cell_for(i))
            cell.font = font or S.FORMULA_FONT
            cell.number_format = number_format
        return row + 1

    # --- build ------------------------------------------------------------- #

    def build(self, path: Path) -> Path:
        default = self.wb.active
        if default is not None:
            self.wb.remove(default)
        for title in SHEETS:
            self.wb.create_sheet(title)

        # Order matters: each sheet records the rows later sheets point at.
        self._assumptions()
        self._revenue_build()
        self._headcount()
        self._pnl()
        self._cash_flow()
        self._summary()
        self._sources()

        path.parent.mkdir(parents=True, exist_ok=True)
        self.wb.save(path)
        return path

    # ------------------------------------------------------------------ #

    def _assumptions(self) -> None:
        ws = self.wb["Assumptions"]
        company = self.model.assumptions.company
        row = self._chrome(
            ws,
            f"{company.name} — {self.years}-Year CFO Financial Model | Assumptions",
            S.UNITS_NOTE,
            label_width=S.WIDE_LABEL_WIDTH,
        )
        trailing = self._col(self.years)
        ws.cell(row=S.YEAR_ROW, column=trailing, value="Source").font = S.YEAR_FONT
        ws.cell(row=S.YEAR_ROW, column=trailing + 1, value="Basis / note").font = S.YEAR_FONT
        ws.column_dimensions[get_column_letter(trailing)].width = 12
        ws.column_dimensions[get_column_letter(trailing + 1)].width = 70

        for section in self.plan.sections:
            row = self._section(ws, row, section.title)
            for line in section.lines:
                row = self._assumption_row(ws, row, line)
            row += 1

    def _assumption_row(self, ws: Worksheet, row: int, line: Line) -> int:
        label = ws.cell(row=row, column=1, value=line.label)
        label.font = S.LABEL_FONT
        if line.flagged:
            label.fill = S.FLAG

        for i in range(self.years):
            precision = PRECISION[line.kind]
            cell = ws.cell(row=row, column=self._col(i), value=round(line.values[i], precision))
            cell.font = S.INPUT_FONT
            cell.number_format = NUMBER_FORMATS[line.kind]
            if line.flagged:
                cell.fill = S.FLAG

        # Provenance sits beside the number rather than in a separate document.
        trailing = self._col(self.years)
        ws.cell(row=row, column=trailing, value=line.source).font = S.NOTE_FONT
        note = " · ".join(part for part in (line.citation, line.note) if part)
        if note:
            ws.cell(row=row, column=trailing + 1, value=note).font = S.NOTE_FONT

        self.arow[line.key] = row
        return row + 1

    def _a(self, key: str, year: int) -> str:
        return _ref("Assumptions", self._col(year), self.arow[key])

    # ------------------------------------------------------------------ #

    def _revenue_build(self) -> None:
        ws = self.wb["Revenue Build"]
        row = self._chrome(ws, "Revenue Build", S.UNITS_NOTE)

        stream_rows: list[int] = []
        for stream in self.plan.streams:
            row = self._section(ws, row, stream.name)
            volume = row
            row = self._row(
                ws,
                row,
                stream.volume_label,
                lambda i, k=stream.volume_key: f"={self._a(k, i)}",
                number_format=S.COUNT,
                font=S.LINK_FONT,
            )
            price = row
            row = self._row(
                ws,
                row,
                stream.price_label,
                lambda i, k=stream.price_key: f"={self._a(k, i)}",
                font=S.LINK_FONT,
            )
            revenue_row = row
            row = self._row(
                ws,
                row,
                stream.revenue_label,
                lambda i, v=volume, p=price: f"={self._letter(i)}{v}*{self._letter(i)}{p}",
                bold_label=True,
            )
            self.rrow[stream.name] = revenue_row
            stream_rows.append(revenue_row)
            row += 1

        row = self._section(ws, row, "Total revenue")
        total = row
        row = self._row(
            ws,
            row,
            "Total revenue",
            lambda i: "=" + "+".join(f"{self._letter(i)}{r}" for r in stream_rows),
            bold_label=True,
        )
        self.rrow["total"] = total
        self._row(
            ws,
            row,
            "YoY growth",
            lambda i, t=total: (
                '="-"' if i == 0 else f"=IFERROR({self._letter(i)}{t}/{self._letter(i - 1)}{t}-1,0)"
            ),
            number_format=S.PERCENT,
        )

    # ------------------------------------------------------------------ #

    def _headcount(self) -> None:
        ws = self.wb["Headcount"]
        row = self._chrome(ws, "Headcount Plan", S.UNITS_NOTE)
        functions = self.plan.functions

        row = self._section(ws, row, "Headcount by function")
        for function in functions:
            self.hrow[f"hc_{function}"] = row
            row = self._row(
                ws,
                row,
                function,
                lambda i, f=function: f"={self._a(f'hc_{f}', i)}",
                number_format=S.COUNT,
                font=S.LINK_FONT,
            )
        self.hrow["total"] = row
        row = self._row(
            ws,
            row,
            "Total headcount",
            lambda i: "=" + "+".join(f"{self._letter(i)}{self.hrow[f'hc_{f}']}" for f in functions),
            number_format=S.COUNT,
            bold_label=True,
        )
        row += 1

        row = self._section(ws, row, "Annual cash compensation by function ($000)")
        for function in functions:
            self.hrow[f"comp_{function}"] = row
            row = self._row(
                ws,
                row,
                f"{function} compensation",
                lambda i, f=function: (
                    f"={self._letter(i)}{self.hrow[f'hc_{f}']}*{self._a(f'comp_{f}', i)}"
                ),
            )
        self.hrow["total_comp"] = row
        self._row(
            ws,
            row,
            "Total cash compensation",
            lambda i: (
                "=" + "+".join(f"{self._letter(i)}{self.hrow[f'comp_{f}']}" for f in functions)
            ),
            bold_label=True,
        )

    def _h(self, key: str, year: int) -> str:
        return _ref("Headcount", self._col(year), self.hrow[key])

    # ------------------------------------------------------------------ #

    def _pnl(self) -> None:
        ws = self.wb["P&L"]
        row = self._chrome(ws, "Profit & Loss ($000)", S.UNITS_NOTE)

        row = self._section(ws, row, "Revenue")
        stream_rows: list[int] = []
        for stream in self.plan.streams:
            stream_rows.append(row)
            row = self._row(
                ws,
                row,
                stream.revenue_label,
                lambda i, s=stream.name: f"={_ref('Revenue Build', self._col(i), self.rrow[s])}",
                font=S.LINK_FONT,
            )
        self.prow["revenue"] = row
        row = self._row(
            ws,
            row,
            "Total revenue",
            lambda i: "=" + "+".join(f"{self._letter(i)}{r}" for r in stream_rows),
            bold_label=True,
        )
        row += 1

        # Costs are negative so subtotals add. That is the convention that lets a
        # reader check a column by summing it, and it makes a sign error visible.
        row = self._section(ws, row, "Cost of goods sold")
        self.prow["cogs"] = row
        row = self._row(
            ws,
            row,
            "Cost of revenue",
            lambda i: f"=-{self._letter(i)}{self.prow['revenue']}*{self._a('cogs_pct', i)}",
        )
        self.prow["gross"] = row
        row = self._row(
            ws,
            row,
            "Gross profit",
            lambda i: (
                f"={self._letter(i)}{self.prow['revenue']}+{self._letter(i)}{self.prow['cogs']}"
            ),
            bold_label=True,
        )
        self.prow["gross_margin"] = row
        row = self._row(
            ws,
            row,
            "Gross margin",
            lambda i: (
                f"=IFERROR({self._letter(i)}{self.prow['gross']}"
                f"/{self._letter(i)}{self.prow['revenue']},0)"
            ),
            number_format=S.PERCENT,
        )
        row += 1

        row = self._section(ws, row, "Operating expenses")
        opex_rows: list[int] = []
        for label, group, non_personnel in (
            ("R&D", RD_FUNCTIONS, "opex_R&D non-personnel"),
            ("Sales & marketing", SM_FUNCTIONS, "opex_Sales & marketing programmes"),
            ("G&A", GA_FUNCTIONS, "opex_G&A non-personnel"),
        ):
            active = [f for f in group if f in self.plan.functions]
            if active:
                opex_rows.append(row)
                row = self._row(
                    ws,
                    row,
                    f"{label} personnel",
                    lambda i, a=active: (
                        "=-(" + "+".join(self._h(f"comp_{f}", i) for f in a) + ")"
                        f"*(1+{self._a('burden_pct', i)})"
                    ),
                )
            if non_personnel in self.arow:
                opex_rows.append(row)
                row = self._row(
                    ws,
                    row,
                    f"{label} non-personnel",
                    lambda i, k=non_personnel: f"=-{self._a(k, i)}",
                    font=S.LINK_FONT,
                )

        self.prow["opex"] = row
        row = self._row(
            ws,
            row,
            "Total operating expenses",
            lambda i: "=" + "+".join(f"{self._letter(i)}{r}" for r in opex_rows),
            bold_label=True,
        )
        self.prow["ebitda"] = row
        row = self._row(
            ws,
            row,
            "EBITDA",
            lambda i: (
                f"={self._letter(i)}{self.prow['gross']}+{self._letter(i)}{self.prow['opex']}"
            ),
            bold_label=True,
        )
        self.prow["ebitda_margin"] = row
        row = self._row(
            ws,
            row,
            "EBITDA margin",
            lambda i: (
                f"=IFERROR({self._letter(i)}{self.prow['ebitda']}"
                f"/{self._letter(i)}{self.prow['revenue']},0)"
            ),
            number_format=S.PERCENT,
        )
        row += 1

        # EBITDA is where most startup models stop. Carrying on to net income is what
        # makes the statement credible to someone who reads real financials, and the
        # cash statement needs the tax line, which does not exist until here.
        row = self._section(ws, row, "Below EBITDA")
        life = round(self.model.assumptions.working_capital.capex_depreciation_years.value)
        capex_row = self.arow["capex"]

        self.prow["depreciation"] = row
        row = self._row(
            ws,
            row,
            "Depreciation",
            # A range names its sheet once: "Assumptions!B36:D36", never
            # "Assumptions!B36:Assumptions!D36", which Excel rejects outright.
            lambda i, c=capex_row, k=life: (
                f"=-SUM(Assumptions!{get_column_letter(self._col(max(0, i - k + 1)))}{c}"
                f":{get_column_letter(self._col(i))}{c})"
                f"/{self._a('depreciation_years', i)}"
            ),
        )
        self.prow["ebit"] = row
        row = self._row(
            ws,
            row,
            "EBIT",
            lambda i: (
                f"={self._letter(i)}{self.prow['ebitda']}"
                f"+{self._letter(i)}{self.prow['depreciation']}"
            ),
        )

        # Losses accumulate and shelter later profits, which is why a company can be
        # profitable for a year or more before it pays any tax.
        self.prow["nol"] = row
        row = self._row(
            ws,
            row,
            "Loss carryforward balance",
            lambda i: (
                f"=MAX(0,-{self._letter(i)}{self.prow['ebit']})"
                if i == 0
                else f"=MAX(0,{self._letter(i - 1)}{self.prow['nol']}"
                f"-{self._letter(i)}{self.prow['ebit']})"
            ),
        )
        self.prow["tax"] = row
        row = self._row(
            ws,
            row,
            "Tax",
            lambda i: (
                f"=-MAX(0,{self._letter(i)}{self.prow['ebit']})*{self._a('tax_rate', i)}"
                if i == 0
                else f"=-MAX(0,{self._letter(i)}{self.prow['ebit']}"
                f"-{self._letter(i - 1)}{self.prow['nol']})*{self._a('tax_rate', i)}"
            ),
        )
        self.prow["net_income"] = row
        self._row(
            ws,
            row,
            "Net income",
            lambda i: f"={self._letter(i)}{self.prow['ebit']}+{self._letter(i)}{self.prow['tax']}",
            bold_label=True,
        )

    def _p(self, key: str, year: int) -> str:
        return _ref("P&L", self._col(year), self.prow[key])

    # ------------------------------------------------------------------ #

    def _cash_flow(self) -> None:
        ws = self.wb["Cash Flow"]
        row = self._chrome(
            ws, "Cash Flow & Financing ($000)", S.UNITS_NOTE, label_width=S.WIDE_LABEL_WIDTH
        )

        row = self._section(ws, row, "Operating cash flow")
        ebitda = row
        row = self._row(ws, row, "EBITDA", lambda i: f"={self._p('ebitda', i)}", font=S.LINK_FONT)

        ar = row
        row = self._row(
            ws,
            row,
            "Accounts receivable",
            lambda i: f"=-{self._p('revenue', i)}*{self._a('dso', i)}/365",
        )
        inventory = row
        row = self._row(
            ws,
            row,
            "Inventory",
            lambda i: f"=-ABS({self._p('cogs', i)})*{self._a('inventory_days', i)}/365",
        )
        payable = row
        row = self._row(
            ws,
            row,
            "Accounts payable",
            lambda i: f"=ABS({self._p('cogs', i)}+{self._p('opex', i)})*{self._a('dpo', i)}/365",
        )
        # Annual prepayment is a liability, and a growing prepaid base funds the
        # company. Omitting it understates cash badly for a subscription business.
        deferred = row
        row = self._row(
            ws,
            row,
            "Deferred revenue",
            lambda i: f"={self._p('revenue', i)}*{self._a('prepaid_pct', i)}*0.5",
        )
        nwc = row
        row = self._row(
            ws,
            row,
            "Net working capital",
            lambda i, a=ar, v=inventory, p=payable, d=deferred: (
                "=" + "+".join(f"{self._letter(i)}{r}" for r in (a, v, p, d))
            ),
        )
        change = row
        row = self._row(
            ws,
            row,
            "Change in NWC",
            lambda i, n=nwc: (
                f"={self._letter(i)}{n}"
                if i == 0
                else f"={self._letter(i)}{n}-{self._letter(i - 1)}{n}"
            ),
        )
        tax_paid = row
        row = self._row(ws, row, "Taxes paid", lambda i: f"={self._p('tax', i)}", font=S.LINK_FONT)
        operating = row
        row = self._row(
            ws,
            row,
            "Operating cash flow",
            lambda i, e=ebitda, c=change, t=tax_paid: (
                f"={self._letter(i)}{e}+{self._letter(i)}{c}+{self._letter(i)}{t}"
            ),
            bold_label=True,
        )
        row += 1

        row = self._section(ws, row, "Investing & financing")
        capex = row
        row = self._row(
            ws,
            row,
            "Capital expenditure",
            lambda i: f"=-{self._a('capex', i)}",
            font=S.LINK_FONT,
        )
        free_cash = row
        row = self._row(
            ws,
            row,
            "Free cash flow before financing",
            lambda i, o=operating, c=capex: f"={self._letter(i)}{o}+{self._letter(i)}{c}",
            bold_label=True,
        )
        equity = row
        row = self._row(
            ws,
            row,
            "Equity financing",
            lambda i: f"={self._a('equity', i)}",
            font=S.LINK_FONT,
        )
        net_change = row
        row = self._row(
            ws,
            row,
            "Net cash change",
            lambda i, f=free_cash, e=equity: f"={self._letter(i)}{f}+{self._letter(i)}{e}",
            bold_label=True,
        )
        row += 1

        row = self._section(ws, row, "Liquidity")
        beginning = row
        ending = row + 1
        row = self._row(
            ws,
            row,
            "Beginning cash",
            lambda i, e=ending: (
                f"={self._a('opening_cash', 0)}" if i == 0 else f"={self._letter(i - 1)}{e}"
            ),
        )
        row = self._row(
            ws,
            row,
            "Ending cash",
            lambda i, b=beginning, n=net_change: f"={self._letter(i)}{b}+{self._letter(i)}{n}",
            bold_label=True,
        )
        self.crow["ending"] = ending
        self.crow["net_change"] = net_change

        floor = MINIMUM_CASH / S.THOUSANDS
        self.crow["shortfall"] = row
        self._row(
            ws,
            row,
            f"Incremental financing required to stay ≥ ${floor:,.0f}k",
            lambda i, e=ending, f=floor: f"=MAX(0,{f}-{self._letter(i)}{e})",
        )

    def _c(self, key: str, year: int) -> str:
        return _ref("Cash Flow", self._col(year), self.crow[key])

    # ------------------------------------------------------------------ #

    def _summary(self) -> None:
        """Links only. A Summary that calculated could disagree with the statement."""
        ws = self.wb["Summary"]
        company = self.model.assumptions.company
        coverage = self.model.assumptions.coverage

        ws.merge_cells(f"A1:{get_column_letter(self._last_col())}1")
        title = ws.cell(row=1, column=1, value=f"{company.name} — CFO Base Case Financial Model")
        title.font = S.TITLE_FONT
        title.fill = S.TITLE_FILL

        ws.cell(
            row=3,
            column=1,
            value=(
                "Blue cells are inputs; green numbers are linked inputs; black cells "
                "are formulas. Yellow-filled inputs were supplied by this tool because "
                f"the deck did not state them. {coverage.summary_line()}"
            ),
        ).font = S.NOTE_FONT

        for i, year in enumerate(self.plan.years):
            cell = ws.cell(row=5, column=self._col(i), value=int(year))
            cell.font = S.YEAR_FONT
            cell.fill = S.YEAR_FILL
            cell.alignment = S.CENTRE

        ws.column_dimensions["A"].width = S.WIDE_LABEL_WIDTH
        for i in range(self.years + 1):
            ws.column_dimensions[get_column_letter(self._col(i))].width = S.YEAR_WIDTH
        ws.freeze_panes = "B6"

        row = self._section(ws, 7, "Financial summary ($000)")
        for label, key, fmt in (
            ("Revenue", "revenue", S.MONEY),
            ("Gross profit", "gross", S.MONEY),
            ("Gross margin", "gross_margin", S.PERCENT),
            ("EBITDA", "ebitda", S.MONEY),
            ("EBITDA margin", "ebitda_margin", S.PERCENT),
        ):
            row = self._row(
                ws,
                row,
                label,
                lambda i, k=key: f"={self._p(k, i)}",
                number_format=fmt,
                font=S.LINK_FONT,
            )
        row = self._row(
            ws, row, "Ending cash", lambda i: f"={self._c('ending', i)}", font=S.LINK_FONT
        )
        row = self._row(
            ws,
            row,
            "Incremental financing required",
            lambda i: f"={self._c('shortfall', i)}",
            font=S.LINK_FONT,
        )
        row += 1

        row = self._section(ws, row, "Operating summary")
        row = self._row(
            ws,
            row,
            "Total headcount",
            lambda i: f"={self._h('total', i)}",
            number_format=S.COUNT,
            font=S.LINK_FONT,
        )
        for stream in self.plan.streams:
            volume = self.arow[stream.volume_key]
            row = self._row(
                ws,
                row,
                stream.volume_label,
                lambda i, v=volume: f"={_ref('Assumptions', self._col(i), v)}",
                number_format=S.COUNT,
                font=S.LINK_FONT,
            )
        row += 1

        if self.plan.observations:
            ws.merge_cells(f"A{row}:{get_column_letter(self._last_col())}{row}")
            header = ws.cell(row=row, column=1, value="Key CFO observations")
            header.font = S.OBSERVATION_FONT
            header.fill = S.OBSERVATION_FILL
            row += 1
            for i, observation in enumerate(self.plan.observations, start=1):
                ws.merge_cells(f"A{row}:{get_column_letter(self._last_col())}{row}")
                ws.cell(row=row, column=1, value=f"{i}. {observation}").font = S.LABEL_FONT
                row += 1

        row += 1
        ws.merge_cells(f"A{row}:{get_column_letter(self._last_col())}{row}")
        ws.cell(
            row=row,
            column=1,
            value=(
                "Basis: this workbook is an annual restatement of a monthly model. "
                "Revenue, gross profit and EBITDA reconcile to it exactly. Cash and "
                "tax differ by a few percent, because working capital and the loss "
                "carryforward depend on the path within a year, which an annual grid "
                "cannot carry. Break-even month, peak cash need and runway on the "
                "one-pager come from the monthly build."
            ),
        ).font = S.NOTE_FONT
        row += 2

        ws.cell(
            row=row,
            column=1,
            value=(
                f"pitchdeck-cfo {MODEL_VERSION} · Built from figures stated in an "
                f"investor deck and filled out with labelled benchmarks. Not audited "
                f"financial information, and not a forecast the company has endorsed."
            ),
        ).font = S.NOTE_FONT

    # ------------------------------------------------------------------ #

    def _sources(self) -> None:
        ws = self.wb["Sources & Notes"]
        ws.merge_cells("A1:E1")
        title = ws.cell(row=1, column=1, value="Sources & Model Notes")
        title.font = S.TITLE_FONT
        title.fill = S.TITLE_FILL

        ws.cell(
            row=3,
            column=1,
            value=(
                "Source hierarchy: stated in the deck > derived from stated figures > "
                "published benchmark > planning convention."
            ),
        ).font = S.NOTE_FONT

        for i, header in enumerate(
            ("Source", "Location", "Model use", "Confidence", "Notes"), start=1
        ):
            cell = ws.cell(row=5, column=i, value=header)
            cell.font = S.SECTION_FONT
            cell.fill = S.SECTION_FILL

        for width, column in zip((34, 30, 52, 14, 60), "ABCDE", strict=True):
            ws.column_dimensions[column].width = width
        ws.freeze_panes = "A6"

        row = 6
        for entry in source_rows(self.model):
            for i, value in enumerate(entry, start=1):
                cell = ws.cell(row=row, column=i, value=value)
                cell.font = S.LABEL_FONT
                cell.alignment = S.WRAP_TOP
            row += 1


def write(model: FinancialModel, path: Path) -> Path:
    """Render the model to a formula-driven workbook."""
    return WorkbookWriter(model).build(path)
