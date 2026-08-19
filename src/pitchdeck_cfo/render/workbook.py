"""The financial model workbook -- formulas, not values.

This is the source of truth the PDF renders from, and the requirement that makes it
worth anything is that **every downstream cell is a live Excel formula referencing the
Assumptions tab**. Change an assumption in Excel and the whole five-year model
recalculates without Python. A workbook of pasted numbers is a screenshot with
gridlines; an investor cannot stress-test it, so it does not do the job.

That means the model is implemented twice: once in `model/` for Python and once here
in Excel formulas. The duplication is deliberate and the integrity tests cover it --
`tests/render/` rebuilds the workbook and checks the formula results agree with the
Python model.

Conventions, which are the standard ones so a financial analyst can read this without
being told: **blue text is an input you may change**, black text is a formula.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.worksheet import Worksheet

from pitchdeck_cfo import MODEL_VERSION
from pitchdeck_cfo.assume.overrides import resolved_names
from pitchdeck_cfo.assume.schema import (
    Function,
    HardwareRevenue,
    LifeSciencesRevenue,
    SaaSRevenue,
)
from pitchdeck_cfo.model.build import FinancialModel
from pitchdeck_cfo.model.headcount import FUNCTIONS, STATIC_FUNCTIONS

# --------------------------------------------------------------------------- #
# style
# --------------------------------------------------------------------------- #

INPUT_FONT = Font(color="0000CC", name="Calibri", size=10)
FORMULA_FONT = Font(color="000000", name="Calibri", size=10)
LABEL_FONT = Font(color="000000", name="Calibri", size=10)
BOLD = Font(bold=True, name="Calibri", size=10)
TITLE = Font(bold=True, size=13, name="Calibri")
SECTION = Font(bold=True, size=10, color="444444", name="Calibri")
NOTE = Font(italic=True, size=9, color="666666", name="Calibri")

HEADER_FILL = PatternFill("solid", fgColor="F2F2F2")
TOTAL_TOP = Border(top=Side(style="thin", color="999999"))

MONEY = "#,##0;(#,##0)"
MONEY_CENTS = "#,##0.00;(#,##0.00)"
PERCENT = '0.0"%"'
NUMBER = "#,##0.0"
COUNT = "#,##0"

LABEL_COL = 1
UNIT_COL = 2
FIRST_MONTH_COL = 3
FIRST_YEAR_COL = 3

SHEETS = (
    "README",
    "Assumptions",
    "Revenue",
    "Headcount",
    "COGS",
    "Opex",
    "PnL",
    "Cashflow",
    "Metrics",
    "DeckFacts",
)


def _excel_name(path: str) -> str:
    """A defined name Excel will accept, derived from a dotted assumption path."""
    cleaned = re.sub(r"[^0-9A-Za-z]+", "_", path).strip("_")
    if cleaned and cleaned[0].isdigit():
        cleaned = "a_" + cleaned
    return cleaned[:255]


@dataclass(frozen=True)
class Row:
    """A named line on a sheet, so other sheets can point at it by meaning."""

    sheet: str
    row: int

    def at(self, month: int) -> str:
        """Reference to this line in a given month column, zero-based."""
        return f"{self.sheet}!{get_column_letter(FIRST_MONTH_COL + month)}{self.row}"

    def local(self, month: int) -> str:
        return f"{get_column_letter(FIRST_MONTH_COL + month)}{self.row}"

    def local_range(self, start: int, end: int) -> str:
        first = get_column_letter(FIRST_MONTH_COL + start)
        last = get_column_letter(FIRST_MONTH_COL + end)
        return f"{first}{self.row}:{last}{self.row}"

    def range(self, start: int, end: int) -> str:
        return f"{self.sheet}!{self.local_range(start, end)}"


class WorkbookWriter:
    """Builds the workbook. One instance per model; not reusable."""

    def __init__(self, model: FinancialModel) -> None:
        self.model = model
        self.months = len(model.month_labels)
        self.years = len(model.year_labels)
        self.wb = Workbook()
        self.rows: dict[str, Row] = {}
        self.names: dict[str, str] = {}

    # --- primitives -------------------------------------------------------- #

    def _sheet(self, title: str) -> Worksheet:
        return self.wb[title]

    def _month_header(self, ws: Worksheet, row: int) -> int:
        ws.cell(row=row, column=LABEL_COL, value="Month").font = SECTION
        for i, label in enumerate(self.model.month_labels):
            cell = ws.cell(row=row, column=FIRST_MONTH_COL + i, value=label)
            cell.font = BOLD
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center")
        ws.freeze_panes = f"{get_column_letter(FIRST_MONTH_COL)}{row + 1}"
        return row + 1

    def _year_header(self, ws: Worksheet, row: int) -> int:
        for i, label in enumerate(self.model.year_labels):
            cell = ws.cell(row=row, column=FIRST_YEAR_COL + i, value=label)
            cell.font = BOLD
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center")
        return row + 1

    def series(
        self,
        ws: Worksheet,
        row: int,
        label: str,
        formula: Any,
        *,
        key: str | None = None,
        number_format: str = MONEY,
        unit: str = "",
        bold: bool = False,
        rule_above: bool = False,
    ) -> int:
        """Write one monthly line, `formula(t)` giving the formula for month t."""
        name_cell = ws.cell(row=row, column=LABEL_COL, value=label)
        name_cell.font = BOLD if bold else LABEL_FONT
        ws.cell(row=row, column=UNIT_COL, value=unit).font = NOTE

        for t in range(self.months):
            cell = ws.cell(row=row, column=FIRST_MONTH_COL + t, value=formula(t))
            cell.font = BOLD if bold else FORMULA_FONT
            cell.number_format = number_format
            if rule_above:
                cell.border = TOTAL_TOP
        if rule_above:
            name_cell.border = TOTAL_TOP

        self.rows[key or f"{ws.title}:{label}"] = Row(ws.title, row)
        return row + 1

    def annual_series(
        self,
        ws: Worksheet,
        row: int,
        label: str,
        formula: Any,
        *,
        key: str | None = None,
        number_format: str = MONEY,
        bold: bool = False,
        rule_above: bool = False,
    ) -> int:
        name_cell = ws.cell(row=row, column=LABEL_COL, value=label)
        name_cell.font = BOLD if bold else LABEL_FONT
        for y in range(self.years):
            cell = ws.cell(row=row, column=FIRST_YEAR_COL + y, value=formula(y))
            cell.font = BOLD if bold else FORMULA_FONT
            cell.number_format = number_format
            if rule_above:
                cell.border = TOTAL_TOP
        if rule_above:
            name_cell.border = TOTAL_TOP
        self.rows[key or f"{ws.title}:{label}"] = Row(ws.title, row)
        return row + 1

    def n(self, path: str) -> str:
        """The Excel defined name for an assumption, for use inside a formula."""
        return self.names[path]

    def has(self, path: str) -> bool:
        return path in self.names

    # --- the tabs ---------------------------------------------------------- #

    def build(self, path: Path) -> Path:
        default = self.wb.active
        if default is not None:
            self.wb.remove(default)
        for title in SHEETS:
            self.wb.create_sheet(title)

        self._assumptions_tab()
        self._revenue_tab()
        self._headcount_tab()
        self._cogs_tab()
        self._opex_tab()
        self._pnl_tab()
        self._cashflow_tab()
        self._metrics_tab()
        self._deck_facts_tab()
        self._readme_tab()

        path.parent.mkdir(parents=True, exist_ok=True)
        self.wb.save(path)
        return path

    # ------------------------------------------------------------------ #

    def _assumptions_tab(self) -> None:
        ws = self._sheet("Assumptions")
        ws.column_dimensions["A"].width = 42
        ws.column_dimensions["B"].width = 16
        ws.column_dimensions["C"].width = 14
        ws.column_dimensions["D"].width = 13
        ws.column_dimensions["E"].width = 52
        ws.column_dimensions["F"].width = 60

        ws["A1"] = "Assumptions"
        ws["A1"].font = TITLE
        ws["A2"] = (
            "Blue values are inputs. Change one and the whole model recalculates. "
            "Every line says where it came from."
        )
        ws["A2"].font = NOTE

        row = 4
        for header, column in (
            ("Assumption", 1),
            ("Value", 2),
            ("Source", 3),
            ("Confidence", 4),
            ("Citation", 5),
            ("Note", 6),
        ):
            cell = ws.cell(row=row, column=column, value=header)
            cell.font = BOLD
            cell.fill = HEADER_FILL
        row += 1

        resolved = resolved_names(self.model.assumptions)
        for path, value in sorted(resolved.items()):
            # Some Sourced values are narrative rather than numeric -- the calendar
            # basis, for one. They belong on the tab for context but are not model
            # drivers, so they get no defined name and nothing references them.
            if not isinstance(value.value, int | float):
                ws.cell(row=row, column=1, value=path).font = LABEL_FONT
                ws.cell(row=row, column=2, value=str(value.value)).font = NOTE
                ws.cell(row=row, column=3, value=value.source).font = NOTE
                ws.cell(row=row, column=5, value=value.citation or "").font = NOTE
                row += 1
                continue

            name = _excel_name(path)
            ws.cell(row=row, column=1, value=path).font = LABEL_FONT
            cell = ws.cell(row=row, column=2, value=value.value)
            cell.font = INPUT_FONT
            cell.number_format = MONEY_CENTS if abs(value.value) < 1000 else MONEY
            ws.cell(row=row, column=3, value=value.source).font = NOTE
            ws.cell(row=row, column=4, value=value.confidence).font = NOTE
            ws.cell(row=row, column=5, value=value.citation or "").font = NOTE
            ws.cell(row=row, column=6, value=value.note or "").font = NOTE

            self.names[path] = name
            self.wb.defined_names.add(DefinedName(name, attr_text=f"Assumptions!$B${row}"))
            row += 1

        # Derived constants: formulas, so they update when their inputs do.
        row += 1
        ws.cell(row=row, column=1, value="Derived").font = SECTION
        row += 1
        for path, formula, note in self._derived_definitions():
            name = _excel_name(path)
            ws.cell(row=row, column=1, value=path).font = LABEL_FONT
            cell = ws.cell(row=row, column=2, value=formula)
            cell.font = FORMULA_FONT
            cell.number_format = MONEY_CENTS
            ws.cell(row=row, column=3, value="derived").font = NOTE
            ws.cell(row=row, column=6, value=note).font = NOTE
            self.names[path] = name
            self.wb.defined_names.add(DefinedName(name, attr_text=f"Assumptions!$B${row}"))
            row += 1

    def _derived_definitions(self) -> list[tuple[str, str, str]]:
        """Rates the monthly engine needs, expressed as formulas over the inputs."""
        out: list[tuple[str, str, str]] = [
            (
                "derived.dep_life_months",
                f"={self.n('working_capital.capex_depreciation_years')}*12",
                "Useful life in months.",
            ),
            (
                "derived.attrition_uplift",
                f"=1+{self.n('headcount.annual_attrition_pct')}/100*0.25",
                "Replacement-hiring cost carried as an uplift on loaded cost.",
            ),
            (
                "derived.effective_quota",
                f"={self.n('headcount.rep_quota_annual')}"
                f"*{self.n('headcount.quota_attainment_pct')}/100",
                "Quota a rep is planned to actually land.",
            ),
        ]

        revenue = self.model.assumptions.revenue
        if isinstance(revenue, SaaSRevenue):
            out += [
                (
                    "derived.nrr_monthly",
                    f"=({self.n('revenue.net_revenue_retention_pct')}/100)^(1/12)",
                    "Annual NRR compounded down to a month.",
                ),
                (
                    "derived.logo_retention_monthly",
                    f"=(1-{self.n('revenue.logo_churn_annual_pct')}/100)^(1/12)",
                    "Annual logo retention compounded down to a month.",
                ),
            ]
        if isinstance(revenue, HardwareRevenue):
            out += [
                (
                    "derived.channel_realisation",
                    f"={self.n('revenue.direct_sales_mix_pct')}/100"
                    f"+(1-{self.n('revenue.direct_sales_mix_pct')}/100)"
                    f"*(1-{self.n('revenue.distributor_margin_pct')}/100)",
                    "Share of list price realised after the distributor's margin.",
                ),
            ]
        if not isinstance(revenue, LifeSciencesRevenue):
            out += [
                (
                    "derived.growth_decay_monthly",
                    f"=(1-{self.n('revenue.growth_decay_annual_pct')}/100)^(1/12)",
                    "Monthly decay factor applied to the growth rate.",
                ),
            ]
        return out

    # ------------------------------------------------------------------ #

    def _growth_rate(self, t: int) -> str:
        """The decaying monthly growth rate in month t, as a formula."""
        revenue = self.model.assumptions.revenue
        base = (
            self.n("revenue.new_logo_growth_monthly_pct")
            if isinstance(revenue, SaaSRevenue)
            else self.n("revenue.unit_growth_monthly_pct")
        )
        floor = self.n("revenue.terminal_growth_monthly_pct")
        return f"MAX({base}*{self.n('derived.growth_decay_monthly')}^{t},{floor})"

    def _revenue_tab(self) -> None:
        ws = self._sheet("Revenue")
        ws.column_dimensions["A"].width = 34
        ws.column_dimensions["B"].width = 12
        ws["A1"] = "Revenue Build"
        ws["A1"].font = TITLE
        row = self._month_header(ws, 3)

        revenue = self.model.assumptions.revenue
        if isinstance(revenue, SaaSRevenue):
            row = self._saas_rows(ws, row)
        elif isinstance(revenue, HardwareRevenue):
            row = self._hardware_rows(ws, row)
        else:
            row = self._life_sciences_rows(ws, row)

    def _saas_rows(self, ws: Worksheet, row: int) -> int:
        new_logos = Row(ws.title, row)
        row = self.series(
            ws,
            row,
            "New logos",
            lambda t: (
                f"={self.n('revenue.new_logos_month_1')}"
                if t == 0
                else f"={new_logos.local(t - 1)}*(1+{self._growth_rate(t - 1)}/100)"
            ),
            key="rev.new_logos",
            number_format=NUMBER,
            unit="logos",
        )

        row = self.series(
            ws,
            row,
            "New ARR",
            lambda t: f"={self.rows['rev.new_logos'].local(t)}*{self.n('revenue.arpu_annual')}",
            key="rev.new_arr",
            unit="$/yr",
        )

        arr = Row(ws.title, row)
        row = self.series(
            ws,
            row,
            "ARR",
            lambda t: (
                f"={self.n('revenue.starting_arr')}*{self.n('derived.nrr_monthly')}"
                f"+{self.rows['rev.new_arr'].local(0)}"
                if t == 0
                else f"={arr.local(t - 1)}*{self.n('derived.nrr_monthly')}"
                f"+{self.rows['rev.new_arr'].local(t)}"
            ),
            key="rev.arr",
            unit="$/yr",
            bold=True,
        )

        customers = Row(ws.title, row)
        row = self.series(
            ws,
            row,
            "Ending customers",
            lambda t: (
                f"={self.n('revenue.starting_customers')}"
                f"*{self.n('derived.logo_retention_monthly')}"
                f"+{self.rows['rev.new_logos'].local(0)}"
                if t == 0
                else f"={customers.local(t - 1)}*{self.n('derived.logo_retention_monthly')}"
                f"+{self.rows['rev.new_logos'].local(t)}"
            ),
            key="rev.customers",
            number_format=NUMBER,
            unit="accounts",
        )

        row = self.series(
            ws,
            row,
            "Revenue (recognised)",
            lambda t: f"={self.rows['rev.arr'].local(t)}/12",
            key="rev.recognised",
            bold=True,
            rule_above=True,
        )

        deferred = Row(ws.title, row)
        row = self.series(
            ws,
            row,
            "Deferred revenue balance",
            lambda t: (
                f"={self.rows['rev.arr'].local(t)}"
                f"*{self.n('revenue.annual_prepay_mix_pct')}/100*0.5"
            ),
            key="rev.deferred",
        )

        row = self.series(
            ws,
            row,
            "Billings",
            lambda t: (
                f"={self.rows['rev.recognised'].local(t)}"
                if t == 0
                else f"={self.rows['rev.recognised'].local(t)}"
                f"+{deferred.local(t)}-{deferred.local(t - 1)}"
            ),
            key="rev.billings",
        )
        return row

    def _hardware_rows(self, ws: Worksheet, row: int) -> int:
        units = Row(ws.title, row)
        row = self.series(
            ws,
            row,
            "Units placed",
            lambda t: (
                f"={self.n('revenue.units_month_1')}"
                if t == 0
                else f"={units.local(t - 1)}*(1+{self._growth_rate(t - 1)}/100)"
            ),
            key="rev.units",
            number_format=NUMBER,
            unit="units",
        )

        installed = Row(ws.title, row)
        row = self.series(
            ws,
            row,
            "Installed base",
            lambda t: (
                f"={self.n('revenue.installed_base_start')}+{units.local(0)}"
                if t == 0
                else f"={installed.local(t - 1)}+{units.local(t)}"
            ),
            key="rev.installed_base",
            number_format=NUMBER,
            unit="units",
        )

        row = self.series(
            ws,
            row,
            "Device revenue",
            lambda t: (
                f"={units.local(t)}*{self.n('revenue.device_asp')}"
                f"*{self.n('derived.channel_realisation')}"
            ),
            key="rev.device",
        )

        row = self.series(
            ws,
            row,
            "Consumable units",
            lambda t: (
                f"={installed.local(t)}*{self.n('revenue.consumables_per_device_per_year')}/12"
            ),
            key="rev.consumable_units",
            number_format=NUMBER,
            unit="units",
        )

        row = self.series(
            ws,
            row,
            "Consumable revenue",
            lambda t: (
                f"={self.rows['rev.consumable_units'].local(t)}"
                f"*{self.n('revenue.consumable_price')}*{self.n('derived.channel_realisation')}"
            ),
            key="rev.consumables",
        )

        row = self.series(
            ws,
            row,
            "Revenue (recognised)",
            lambda t: (
                f"={self.rows['rev.device'].local(t)}+{self.rows['rev.consumables'].local(t)}"
            ),
            key="rev.recognised",
            bold=True,
            rule_above=True,
        )

        row = self.series(
            ws,
            row,
            "New recurring revenue",
            lambda t: f"={self.rows['rev.consumables'].local(t)}*12",
            key="rev.new_arr",
            unit="$/yr",
        )
        self.rows["rev.customers"] = self.rows["rev.installed_base"]
        self.rows["rev.billings"] = self.rows["rev.recognised"]
        self.rows["rev.deferred"] = Row(ws.title, row)
        row = self.series(ws, row, "Deferred revenue balance", lambda t: "=0", key="rev.deferred")
        return row

    def _life_sciences_rows(self, ws: Worksheet, row: int) -> int:
        row = self.series(
            ws,
            row,
            "Partnership revenue",
            lambda t: f"={self.n('revenue.partnership_upfront')}/12" if t < 12 else "=0",
            key="rev.recognised",
            bold=True,
        )
        row = self.series(
            ws,
            row,
            "Grant funding (non-dilutive)",
            lambda t: f"={self.n('revenue.non_dilutive_funding')}/12" if t < 12 else "=0",
            key="rev.grant",
        )

        phases = self.model.assumptions.revenue
        assert isinstance(phases, LifeSciencesRevenue)
        for i, phase in enumerate(phases.program_phases):
            start = round(phase.start_month_offset.value)
            duration = max(1, round(phase.duration_months.value))
            cost_name = self.n(f"revenue.program_phases[{phase.name}].total_cost")
            duration_name = self.n(f"revenue.program_phases[{phase.name}].duration_months")
            row = self.series(
                ws,
                row,
                f"Programme spend: {phase.name}"[:60],
                lambda t, s=start, d=duration, c=cost_name, dn=duration_name: (
                    f"={c}/{dn}" if s <= t < s + d else "=0"
                ),
                key=f"rev.program_{i}",
            )

        row = self.series(
            ws,
            row,
            "Total programme spend",
            lambda t: (
                "="
                + "+".join(
                    self.rows[f"rev.program_{i}"].local(t)
                    for i in range(len(phases.program_phases))
                )
                if phases.program_phases
                else "=0"
            ),
            key="rev.program_total",
            bold=True,
            rule_above=True,
        )

        for key, label in (
            ("rev.new_arr", "New recurring revenue"),
            ("rev.customers", "Customers"),
            ("rev.deferred", "Deferred revenue balance"),
        ):
            row = self.series(ws, row, label, lambda t: "=0", key=key)
        self.rows["rev.billings"] = self.rows["rev.recognised"]
        return row

    # ------------------------------------------------------------------ #

    def _headcount_tab(self) -> None:
        ws = self._sheet("Headcount")
        ws.column_dimensions["A"].width = 30
        ws.column_dimensions["B"].width = 12
        ws["A1"] = "Headcount Plan"
        ws["A1"].font = TITLE
        ws["A2"] = "Hiring is triggered by the plan. Each line states its driver."
        ws["A2"].font = NOTE
        row = self._month_header(ws, 4)

        rev = self.rows["rev.new_arr"]
        customers = self.rows["rev.customers"]
        recognised = self.rows["rev.recognised"]

        # Sales: quota coverage, smoothed over a quarter, pulled forward by the ramp.
        ramp = round(self.model.assumptions.headcount.rep_ramp_months.value)

        def sales(t: int) -> str:
            source = min(t + ramp, self.months - 1)
            lo = max(0, source - 1)
            hi = min(self.months - 1, source + 1)
            window = f"AVERAGE({rev.sheet}!{rev.local_range(lo, hi).split('!')[-1]})"
            required = (
                f"IF({self.n('derived.effective_quota')}=0,0,"
                f"CEILING({window}/{self.n('derived.effective_quota')},1))"
            )
            floor = self.n("headcount.starting.Sales")
            if t == 0:
                return f"=MAX({floor},{required})"
            prior = self.rows["hc.Sales"].local(t - 1)
            return f"=MAX({prior},{floor},{required})"

        row = self._function_row(ws, row, "Sales", sales)

        def customer_success(t: int) -> str:
            per = self.n("headcount.accounts_per_csm")
            required = f"IF({per}=0,0,CEILING({customers.at(t)}/{per},1))"
            floor = self.n("headcount.starting.Customer Success")
            if t == 0:
                return f"=MAX({floor},{required})"
            return f"=MAX({self.rows['hc.Customer Success'].local(t - 1)},{floor},{required})"

        row = self._function_row(ws, row, "Customer Success", customer_success)

        def engineering(t: int) -> str:
            per = self.n("headcount.revenue_per_engineer")
            exponent = self.n("headcount.scale_exponent")
            # Bracketed deliberately: this string is interpolated into a division,
            # and unbracketed "A*12" would parse as (x/A)*12 rather than x/(A*12).
            first_revenue = f"({recognised.at(0)}*12)"
            roadmap = (
                f"{self.n('headcount.engineers_per_product_line')}"
                f"*{self.n('headcount.product_lines')}"
            )
            scaled = (
                f"IF(OR({per}=0,{first_revenue}<=0),0,"
                f"CEILING(({first_revenue}/{per})"
                f"*(({recognised.at(t)}*12)/{first_revenue})^{exponent},1))"
            )
            floor = self.n("headcount.starting.Engineering")
            if t == 0:
                return f"=MAX({floor},{roadmap},{scaled})"
            return f"=MAX({self.rows['hc.Engineering'].local(t - 1)},{floor},{roadmap},{scaled})"

        row = self._function_row(ws, row, "Engineering", engineering)

        def marketing(t: int) -> str:
            per = self.n("headcount.sales_per_marketing_hire")
            required = f"IF({per}=0,0,CEILING({self.rows['hc.Sales'].local(t)}/{per},1))"
            floor = self.n("headcount.starting.Marketing")
            if t == 0:
                return f"=MAX({floor},{required})"
            return f"=MAX({self.rows['hc.Marketing'].local(t - 1)},{floor},{required})"

        row = self._function_row(ws, row, "Marketing", marketing)

        for function in STATIC_FUNCTIONS:
            row = self._function_row(
                ws,
                row,
                function,
                lambda t, f=function: f"={self.n(f'headcount.starting.{f}')}",
            )

        supported: tuple[Function, ...] = tuple(f for f in FUNCTIONS if f != "G&A")

        def general_admin(t: int) -> str:
            per = self.n("headcount.ftes_per_ga_hire")
            total = "+".join(self.rows[f"hc.{f}"].local(t) for f in supported)
            required = f"IF({per}=0,0,CEILING(({total})/{per},1))"
            floor = self.n("headcount.starting.G&A")
            if t == 0:
                return f"=MAX({floor},{required})"
            return f"=MAX({self.rows['hc.G&A'].local(t - 1)},{floor},{required})"

        row = self._function_row(ws, row, "G&A", general_admin)

        row = self.series(
            ws,
            row,
            "Total headcount",
            lambda t: "=" + "+".join(self.rows[f"hc.{f}"].local(t) for f in FUNCTIONS),
            key="hc.total",
            number_format=COUNT,
            unit="FTE",
            bold=True,
            rule_above=True,
        )

        row += 1
        ws.cell(row=row, column=LABEL_COL, value="Fully loaded cost").font = SECTION
        row += 1
        for function in FUNCTIONS:
            row = self.series(
                ws,
                row,
                f"{function} cost",
                lambda t, f=function: (
                    f"={self.rows[f'hc.{f}'].local(t)}"
                    f"*{self.n(f'headcount.base_salary.{f}')}/12"
                    f"*{self.n('headcount.loaded_multiplier')}"
                    f"*{self.n('derived.attrition_uplift')}"
                ),
                key=f"hccost.{function}",
            )
        row = self.series(
            ws,
            row,
            "Total loaded cost",
            lambda t: "=" + "+".join(self.rows[f"hccost.{f}"].local(t) for f in FUNCTIONS),
            key="hccost.total",
            bold=True,
            rule_above=True,
        )

        row += 1
        ws.cell(row=row, column=LABEL_COL, value="Hiring drivers").font = SECTION
        row += 1
        for driver_name, note in self.model.headcount_drivers.items():
            ws.cell(row=row, column=LABEL_COL, value=driver_name).font = LABEL_FONT
            ws.cell(row=row, column=FIRST_MONTH_COL, value=note).font = NOTE
            row += 1

    def _function_row(self, ws: Worksheet, row: int, function: Function, formula: Any) -> int:
        self.rows[f"hc.{function}"] = Row(ws.title, row)
        return self.series(
            ws, row, function, formula, key=f"hc.{function}", number_format=COUNT, unit="FTE"
        )

    # ------------------------------------------------------------------ #

    def _cogs_tab(self) -> None:
        ws = self._sheet("COGS")
        ws.column_dimensions["A"].width = 34
        ws.column_dimensions["B"].width = 12
        ws["A1"] = "Cost of Revenue"
        ws["A1"].font = TITLE
        ws["A2"] = (
            "Infrastructure scales at revenue^exponent. Below 1.0 that is what "
            "produces margin expansion; set it to 1.0 and the expansion disappears."
        )
        ws["A2"].font = NOTE
        row = self._month_header(ws, 4)

        rev = self.rows["rev.recognised"]
        first = f"{rev.at(0)}"
        components: list[str] = []

        hosting_pct = self.n("cogs.hosting_pct_of_revenue")
        exponent = self.n("cogs.hosting_scale_exponent")
        row = self.series(
            ws,
            row,
            "Hosting & infrastructure",
            lambda t: (
                f"=IF(OR({hosting_pct}=0,{first}<=0),0,"
                f"({hosting_pct}/100)*{first}*({rev.at(t)}/{first})^{exponent})"
            ),
            key="cogs.hosting",
        )
        components.append("cogs.hosting")

        for label, key, pct_path in (
            ("Support & success", "cogs.support", "cogs.support_pct_of_revenue"),
            ("Payment processing", "cogs.processing", "cogs.payment_processing_pct"),
            ("Warranty & field service", "cogs.warranty", "cogs.warranty_pct_of_revenue"),
        ):
            row = self.series(
                ws,
                row,
                label,
                lambda t, p=pct_path: f"={rev.at(t)}*{self.n(p)}/100",
                key=key,
            )
            components.append(key)

        if "rev.device" in self.rows:
            device = self.rows["rev.device"]
            row = self.series(
                ws,
                row,
                "Bill of materials",
                lambda t: f"={device.at(t)}*{self.n('cogs.bom_pct_of_asp')}/100",
                key="cogs.bom",
            )
            components.append("cogs.bom")

            consumables = self.rows["rev.consumables"]
            row = self.series(
                ws,
                row,
                "Consumable cost",
                lambda t: (
                    f"={consumables.at(t)}*(1-{self.n('cogs.consumable_gross_margin_pct')}/100)"
                ),
                key="cogs.consumable",
            )
            components.append("cogs.consumable")

        row = self.series(
            ws,
            row,
            "Total COGS",
            lambda t: "=" + "+".join(self.rows[k].local(t) for k in components),
            key="cogs.total",
            bold=True,
            rule_above=True,
        )

    # ------------------------------------------------------------------ #

    def _opex_tab(self) -> None:
        ws = self._sheet("Opex")
        ws.column_dimensions["A"].width = 38
        ws.column_dimensions["B"].width = 12
        ws["A1"] = "Operating Expense"
        ws["A1"].font = TITLE
        ws["A2"] = (
            "Marketing programme spend follows the new business won, not a flat "
            "percentage of revenue -- which is what makes CAC an output."
        )
        ws["A2"].font = NOTE
        row = self._month_header(ws, 4)

        from pitchdeck_cfo.model.opex import GA_FUNCTIONS, RD_FUNCTIONS, SM_FUNCTIONS

        def cost_sum(functions: tuple[str, ...], t: int) -> str:
            return "+".join(self.rows[f"hccost.{f}"].at(t) for f in functions)

        row = self.series(
            ws,
            row,
            "R&D personnel",
            lambda t: "=" + cost_sum(RD_FUNCTIONS, t),
            key="opex.rd_people",
        )
        row = self.series(
            ws,
            row,
            "R&D tooling & environments",
            lambda t: (
                f"=({self.rows['hc.Engineering'].at(t)}+{self.rows['hc.Research'].at(t)})"
                f"*{self.n('opex.rd_tooling_per_engineer')}/12"
            ),
            key="opex.rd_tooling",
        )
        program_key = "rev.program_total" if "rev.program_total" in self.rows else None
        row = self.series(
            ws,
            row,
            "Development programme",
            lambda t: f"={self.rows[program_key].at(t)}" if program_key else "=0",
            key="opex.rd_program",
        )
        row = self.series(
            ws,
            row,
            "R&D",
            lambda t: (
                f"={self.rows['opex.rd_people'].local(t)}"
                f"+{self.rows['opex.rd_tooling'].local(t)}+{self.rows['opex.rd_program'].local(t)}"
            ),
            key="opex.rd",
            bold=True,
            rule_above=True,
        )

        row = self.series(
            ws,
            row,
            "S&M personnel",
            lambda t: "=" + cost_sum(SM_FUNCTIONS, t),
            key="opex.sm_people",
        )
        row = self.series(
            ws,
            row,
            "Marketing programmes",
            lambda t: (
                f"={self.rows['rev.new_arr'].at(t)}"
                f"*{self.n('opex.marketing_pct_of_new_revenue')}/100"
            ),
            key="opex.sm_programme",
        )
        row = self.series(
            ws,
            row,
            "S&M",
            lambda t: (
                f"={self.rows['opex.sm_people'].local(t)}+{self.rows['opex.sm_programme'].local(t)}"
            ),
            key="opex.sm",
            bold=True,
            rule_above=True,
        )

        row = self.series(
            ws,
            row,
            "G&A personnel",
            lambda t: "=" + cost_sum(GA_FUNCTIONS, t),
            key="opex.ga_people",
        )
        row = self.series(
            ws,
            row,
            "Facilities",
            lambda t: f"={self.rows['hc.total'].at(t)}*{self.n('opex.rent_per_fte')}/12",
            key="opex.facilities",
        )
        row = self.series(
            ws,
            row,
            "Software & tooling",
            lambda t: f"={self.rows['hc.total'].at(t)}*{self.n('opex.software_per_fte')}/12",
            key="opex.software",
        )
        row = self.series(
            ws,
            row,
            "Legal, accounting, insurance & audit",
            lambda t: f"={self.n('opex.ga_fixed_annual')}/12",
            key="opex.ga_fixed",
        )
        row = self.series(
            ws,
            row,
            "G&A",
            lambda t: (
                f"={self.rows['opex.ga_people'].local(t)}"
                f"+{self.rows['opex.facilities'].local(t)}+{self.rows['opex.software'].local(t)}"
                f"+{self.rows['opex.ga_fixed'].local(t)}"
            ),
            key="opex.ga",
            bold=True,
            rule_above=True,
        )

        row = self.series(
            ws,
            row,
            "Total operating expense",
            lambda t: (
                f"={self.rows['opex.rd'].local(t)}+{self.rows['opex.sm'].local(t)}"
                f"+{self.rows['opex.ga'].local(t)}"
            ),
            key="opex.total",
            bold=True,
            rule_above=True,
        )

    # ------------------------------------------------------------------ #

    def _pnl_tab(self) -> None:
        ws = self._sheet("PnL")
        ws.column_dimensions["A"].width = 30
        ws.column_dimensions["B"].width = 12
        ws["A1"] = "Profit & Loss"
        ws["A1"].font = TITLE
        row = self._month_header(ws, 3)

        row = self.series(
            ws,
            row,
            "Revenue",
            lambda t: f"={self.rows['rev.recognised'].at(t)}",
            key="pnl.revenue",
            bold=True,
        )
        row = self.series(
            ws, row, "COGS", lambda t: f"={self.rows['cogs.total'].at(t)}", key="pnl.cogs"
        )
        row = self.series(
            ws,
            row,
            "Gross Profit",
            lambda t: f"={self.rows['pnl.revenue'].local(t)}-{self.rows['pnl.cogs'].local(t)}",
            key="pnl.gross",
            bold=True,
            rule_above=True,
        )
        row = self.series(
            ws,
            row,
            "Gross Margin %",
            lambda t: (
                f"=IF({self.rows['pnl.revenue'].local(t)}=0,0,"
                f"{self.rows['pnl.gross'].local(t)}/{self.rows['pnl.revenue'].local(t)}*100)"
            ),
            key="pnl.gross_margin",
            number_format=PERCENT,
        )
        for label, key, source in (
            ("R&D", "pnl.rd", "opex.rd"),
            ("S&M", "pnl.sm", "opex.sm"),
            ("G&A", "pnl.ga", "opex.ga"),
        ):
            row = self.series(ws, row, label, lambda t, s=source: f"={self.rows[s].at(t)}", key=key)
        row = self.series(
            ws,
            row,
            "Total Opex",
            lambda t: (
                f"={self.rows['pnl.rd'].local(t)}+{self.rows['pnl.sm'].local(t)}"
                f"+{self.rows['pnl.ga'].local(t)}"
            ),
            key="pnl.opex",
            bold=True,
            rule_above=True,
        )
        row = self.series(
            ws,
            row,
            "EBITDA",
            lambda t: f"={self.rows['pnl.gross'].local(t)}-{self.rows['pnl.opex'].local(t)}",
            key="pnl.ebitda",
            bold=True,
            rule_above=True,
        )
        row = self.series(
            ws,
            row,
            "EBITDA Margin %",
            lambda t: (
                f"=IF({self.rows['pnl.revenue'].local(t)}=0,0,"
                f"{self.rows['pnl.ebitda'].local(t)}/{self.rows['pnl.revenue'].local(t)}*100)"
            ),
            key="pnl.ebitda_margin",
            number_format=PERCENT,
        )
        row = self.series(
            ws,
            row,
            "Capex",
            lambda t: (
                f"={self.rows['pnl.revenue'].local(t)}"
                f"*{self.n('working_capital.capex_pct_of_revenue')}/100"
            ),
            key="pnl.capex",
        )
        capex = self.rows["pnl.capex"]

        # A plain 1..N index row. Structural, not an assumption, so it is written as
        # values. It exists so depreciation can be expressed with SUMPRODUCT rather
        # than OFFSET, which not every spreadsheet engine implements.
        index_row = Row(ws.title, row)
        ws.cell(row=row, column=LABEL_COL, value="Month index").font = NOTE
        for t in range(self.months):
            cell = ws.cell(row=row, column=FIRST_MONTH_COL + t, value=t + 1)
            cell.font = NOTE
            cell.number_format = COUNT
        self.rows["pnl.month_index"] = index_row
        row += 1

        life = self.n("derived.dep_life_months")
        capex_range = capex.local_range(0, self.months - 1)
        index_range = index_row.local_range(0, self.months - 1)
        row = self.series(
            ws,
            row,
            "Depreciation",
            lambda t: (
                f"=SUMPRODUCT(({index_range}<={t + 1})*"
                f"({index_range}>{t + 1}-{life})*{capex_range})/{life}"
            ),
            key="pnl.depreciation",
        )
        row = self.series(
            ws,
            row,
            "EBIT",
            lambda t: (
                f"={self.rows['pnl.ebitda'].local(t)}-{self.rows['pnl.depreciation'].local(t)}"
            ),
            key="pnl.ebit",
            rule_above=True,
        )
        row = self.series(ws, row, "Interest", lambda t: "=0", key="pnl.interest")
        row = self.series(
            ws,
            row,
            "Pretax Income",
            lambda t: f"={self.rows['pnl.ebit'].local(t)}-{self.rows['pnl.interest'].local(t)}",
            key="pnl.pretax",
            rule_above=True,
        )

        nol = Row(ws.title, row)
        row = self.series(
            ws,
            row,
            "NOL balance",
            lambda t: (
                f"=MAX(0,{self.n('tax.nol_carryforward_start')}-{self.rows['pnl.pretax'].local(0)})"
                if t == 0
                else f"=MAX(0,{nol.local(t - 1)}-{self.rows['pnl.pretax'].local(t)})"
            ),
            key="pnl.nol",
        )
        row = self.series(
            ws,
            row,
            "Tax",
            lambda t: (
                f"=MAX(0,{self.rows['pnl.pretax'].local(t)}"
                f"-{self.n('tax.nol_carryforward_start') if t == 0 else nol.local(t - 1)})"
                f"*{self.n('tax.blended_rate_pct')}/100"
            ),
            key="pnl.tax",
        )
        row = self.series(
            ws,
            row,
            "Net Income",
            lambda t: f"={self.rows['pnl.pretax'].local(t)}-{self.rows['pnl.tax'].local(t)}",
            key="pnl.net_income",
            bold=True,
            rule_above=True,
        )

        # --- annual roll-up ---
        row += 2
        ws.cell(row=row, column=LABEL_COL, value="Annual roll-up").font = SECTION
        row += 1
        row = self._year_header(ws, row)
        for label, key in (
            ("Revenue", "pnl.revenue"),
            ("COGS", "pnl.cogs"),
            ("Gross Profit", "pnl.gross"),
            ("R&D", "pnl.rd"),
            ("S&M", "pnl.sm"),
            ("G&A", "pnl.ga"),
            ("Total Opex", "pnl.opex"),
            ("EBITDA", "pnl.ebitda"),
            ("Depreciation", "pnl.depreciation"),
            ("Tax", "pnl.tax"),
            ("Net Income", "pnl.net_income"),
        ):
            line = self.rows[key]
            row = self.annual_series(
                ws,
                row,
                label,
                lambda y, r=line: f"=SUM({r.local_range(y * 12, y * 12 + 11)})",
                key=f"annual.{key}",
                bold=label in ("Revenue", "Gross Profit", "EBITDA", "Net Income"),
            )
        row = self.annual_series(
            ws,
            row,
            "Gross Margin %",
            lambda y: (
                f"=IF({self.rows['annual.pnl.revenue'].local(y)}=0,0,"
                f"{self.rows['annual.pnl.gross'].local(y)}"
                f"/{self.rows['annual.pnl.revenue'].local(y)}*100)"
            ),
            key="annual.gross_margin",
            number_format=PERCENT,
        )
        row = self.annual_series(
            ws,
            row,
            "EBITDA Margin %",
            lambda y: (
                f"=IF({self.rows['annual.pnl.revenue'].local(y)}=0,0,"
                f"{self.rows['annual.pnl.ebitda'].local(y)}"
                f"/{self.rows['annual.pnl.revenue'].local(y)}*100)"
            ),
            key="annual.ebitda_margin",
            number_format=PERCENT,
        )

    # ------------------------------------------------------------------ #

    def _cashflow_tab(self) -> None:
        ws = self._sheet("Cashflow")
        ws.column_dimensions["A"].width = 32
        ws.column_dimensions["B"].width = 12
        ws["A1"] = "Cash Flow (indirect)"
        ws["A1"].font = TITLE
        row = self._month_header(ws, 3)

        row = self.series(
            ws, row, "EBITDA", lambda t: f"={self.rows['pnl.ebitda'].at(t)}", key="cf.ebitda"
        )

        ar = Row(ws.title, row)
        row = self.series(
            ws,
            row,
            "Receivables balance",
            lambda t: f"={self.rows['rev.billings'].at(t)}*{self.n('working_capital.dso_days')}/30",
            key="cf.ar_balance",
        )
        ap = Row(ws.title, row)
        row = self.series(
            ws,
            row,
            "Payables balance",
            lambda t: (
                f"=({self.rows['pnl.cogs'].at(t)}+{self.rows['pnl.opex'].at(t)})"
                f"*{self.n('working_capital.dpo_days')}/30"
            ),
            key="cf.ap_balance",
        )
        inv = Row(ws.title, row)
        row = self.series(
            ws,
            row,
            "Inventory balance",
            lambda t: (
                f"={self.rows['pnl.cogs'].at(t)}*{self.n('working_capital.inventory_days')}/30"
            ),
            key="cf.inv_balance",
        )

        row = self.series(
            ws,
            row,
            "Change in AR",
            lambda t: f"=-{ar.local(t)}" if t == 0 else f"=-({ar.local(t)}-{ar.local(t - 1)})",
            key="cf.d_ar",
        )
        row = self.series(
            ws,
            row,
            "Change in Deferred Revenue",
            lambda t: (
                f"={self.rows['rev.deferred'].at(0)}"
                if t == 0
                else f"={self.rows['rev.deferred'].at(t)}-{self.rows['rev.deferred'].at(t - 1)}"
            ),
            key="cf.d_deferred",
        )
        row = self.series(
            ws,
            row,
            "Change in AP",
            lambda t: f"={ap.local(t)}" if t == 0 else f"={ap.local(t)}-{ap.local(t - 1)}",
            key="cf.d_ap",
        )
        row = self.series(
            ws,
            row,
            "Change in Inventory",
            lambda t: f"=-{inv.local(t)}" if t == 0 else f"=-({inv.local(t)}-{inv.local(t - 1)})",
            key="cf.d_inv",
        )
        row = self.series(
            ws, row, "Capex", lambda t: f"=-{self.rows['pnl.capex'].at(t)}", key="cf.capex"
        )
        row = self.series(
            ws, row, "Tax Paid", lambda t: f"=-{self.rows['pnl.tax'].at(t)}", key="cf.tax"
        )
        raise_month = round(self.model.assumptions.financing.raise_month_offset.value)
        row = self.series(
            ws,
            row,
            "Financing",
            lambda t: f"={self.n('financing.raise_amount')}" if t == raise_month else "=0",
            key="cf.financing",
        )
        row = self.series(
            ws,
            row,
            "Net Change in Cash",
            lambda t: (
                "="
                + "+".join(
                    self.rows[k].local(t)
                    for k in (
                        "cf.ebitda",
                        "cf.d_ar",
                        "cf.d_deferred",
                        "cf.d_ap",
                        "cf.d_inv",
                        "cf.capex",
                        "cf.tax",
                        "cf.financing",
                    )
                )
            ),
            key="cf.net_change",
            bold=True,
            rule_above=True,
        )
        cash = Row(ws.title, row)
        row = self.series(
            ws,
            row,
            "Ending Cash",
            lambda t: (
                f"={self.n('financing.cash_on_hand')}+{self.rows['cf.net_change'].local(0)}"
                if t == 0
                else f"={cash.local(t - 1)}+{self.rows['cf.net_change'].local(t)}"
            ),
            key="cf.ending_cash",
            bold=True,
        )

        row += 2
        ws.cell(row=row, column=LABEL_COL, value="Annual roll-up").font = SECTION
        row += 1
        row = self._year_header(ws, row)
        row = self.annual_series(
            ws,
            row,
            "Net Change in Cash",
            lambda y: f"=SUM({self.rows['cf.net_change'].local_range(y * 12, y * 12 + 11)})",
            key="annual.cf.net_change",
        )
        row = self.annual_series(
            ws,
            row,
            "Ending Cash",
            lambda y: f"={self.rows['cf.ending_cash'].local(y * 12 + 11)}",
            key="annual.cf.ending_cash",
            bold=True,
        )
        row += 1
        ws.cell(row=row, column=LABEL_COL, value="Peak cash need").font = LABEL_FONT
        peak = ws.cell(
            row=row,
            column=FIRST_YEAR_COL,
            value=f"=MAX(0,-MIN({self.rows['cf.ending_cash'].local_range(0, self.months - 1)}))",
        )
        peak.number_format = MONEY
        peak.font = BOLD

    # ------------------------------------------------------------------ #

    def _metrics_tab(self) -> None:
        ws = self._sheet("Metrics")
        ws.column_dimensions["A"].width = 34
        ws["A1"] = "Metrics"
        ws["A1"].font = TITLE
        ws["A2"] = "Computed from this model, never restated from the deck."
        ws["A2"].font = NOTE
        row = self._year_header(ws, 4)

        revenue = self.rows["annual.pnl.revenue"]
        gross = self.rows["annual.pnl.gross"]
        ebitda = self.rows["annual.pnl.ebitda"]
        sm = self.rows["annual.pnl.sm"]

        row = self.annual_series(
            ws, row, "Revenue", lambda y: f"={revenue.at(y)}", key="m.revenue", bold=True
        )
        row = self.annual_series(
            ws,
            row,
            "YoY growth %",
            lambda y: (
                '="n/a"'
                if y == 0
                else f'=IF({revenue.at(y - 1)}=0,"n/a",({revenue.at(y)}/{revenue.at(y - 1)}-1)*100)'
            ),
            key="m.growth",
            number_format=PERCENT,
        )
        row = self.annual_series(
            ws,
            row,
            "Gross margin %",
            lambda y: f"=IF({revenue.at(y)}=0,0,{gross.at(y)}/{revenue.at(y)}*100)",
            key="m.gross_margin",
            number_format=PERCENT,
        )
        row = self.annual_series(
            ws,
            row,
            "EBITDA margin %",
            lambda y: f"=IF({revenue.at(y)}=0,0,{ebitda.at(y)}/{revenue.at(y)}*100)",
            key="m.ebitda_margin",
            number_format=PERCENT,
        )
        new_customers = self.rows.get("rev.new_logos") or self.rows.get("rev.units")
        if new_customers is not None:
            row = self.annual_series(
                ws,
                row,
                "CAC",
                lambda y, n=new_customers: (
                    f'=IF(SUM({n.range(y * 12, y * 12 + 11)})=0,"n/a",'
                    f"{sm.at(y)}/SUM({n.range(y * 12, y * 12 + 11)}))"
                ),
                key="m.cac",
            )
        row = self.annual_series(
            ws,
            row,
            "Rule of 40",
            lambda y: (
                '="n/a"'
                if y == 0
                else f'=IF({revenue.at(y - 1)}=0,"n/a",'
                f"({revenue.at(y)}/{revenue.at(y - 1)}-1)*100"
                f"+IF({revenue.at(y)}=0,0,{ebitda.at(y)}/{revenue.at(y)}*100))"
            ),
            key="m.rule40",
            number_format=NUMBER,
        )
        row = self.annual_series(
            ws,
            row,
            "Ending headcount",
            lambda y: f"={self.rows['hc.total'].at(y * 12 + 11)}",
            key="m.headcount",
            number_format=COUNT,
        )
        row = self.annual_series(
            ws,
            row,
            "Revenue per FTE",
            lambda y: (
                f'=IF({self.rows["hc.total"].at(y * 12 + 11)}=0,"n/a",'
                f"{revenue.at(y)}/{self.rows['hc.total'].at(y * 12 + 11)})"
            ),
            key="m.revenue_per_fte",
        )
        row = self.annual_series(
            ws,
            row,
            "Ending cash",
            lambda y: f"={self.rows['annual.cf.ending_cash'].at(y)}",
            key="m.cash",
            bold=True,
        )

    # ------------------------------------------------------------------ #

    def _deck_facts_tab(self) -> None:
        ws = self._sheet("DeckFacts")
        ws.column_dimensions["A"].width = 40
        ws.column_dimensions["B"].width = 20
        ws.column_dimensions["C"].width = 14
        ws.column_dimensions["D"].width = 60
        ws["A1"] = "What the deck actually states"
        ws["A1"].font = TITLE
        ws["A2"] = (
            "Only values sourced from the deck itself. Everything else in this "
            "workbook is derived, benchmarked or supplied by you."
        )
        ws["A2"].font = NOTE

        row = 4
        for i, header in enumerate(("Assumption", "Value", "Citation", "Note"), start=1):
            cell = ws.cell(row=row, column=i, value=header)
            cell.font = BOLD
            cell.fill = HEADER_FILL
        row += 1

        for path, value in sorted(resolved_names(self.model.assumptions).items()):
            if value.source != "deck":
                continue
            ws.cell(row=row, column=1, value=path).font = LABEL_FONT
            cell = ws.cell(row=row, column=2, value=value.value)
            cell.number_format = MONEY
            cell.font = INPUT_FONT
            ws.cell(row=row, column=3, value=value.citation or "").font = NOTE
            ws.cell(row=row, column=4, value=value.note or "").font = NOTE
            row += 1

        if row == 6:
            ws.cell(
                row=row,
                column=1,
                value="The deck states none of the model's core inputs.",
            ).font = NOTE

    # ------------------------------------------------------------------ #

    def _readme_tab(self) -> None:
        ws = self._sheet("README")
        ws.column_dimensions["A"].width = 110
        assumptions = self.model.assumptions
        coverage = assumptions.coverage
        counts = assumptions.by_provenance()

        lines: list[tuple[str, Any]] = [
            (assumptions.company.name, TITLE),
            (
                f"Financial model  ·  {assumptions.company.business_model}  ·  "
                f"{self.years}-year horizon from {assumptions.company.start_month}",
                NOTE,
            ),
            ("", None),
            ("How to use this workbook", SECTION),
            (
                "Blue cells on the Assumptions tab are inputs. Change one and every "
                "tab recalculates -- nothing here is a pasted number.",
                None,
            ),
            (
                "Black cells are formulas. Editing one breaks the chain that makes "
                "this model auditable, so change the assumption instead.",
                None,
            ),
            ("", None),
            ("Where the numbers came from", SECTION),
            (coverage.summary_line(), None),
            (
                f"Across every value in the model: {counts['deck']} from the deck, "
                f"{counts['derived']} derived, {counts['benchmark']} from benchmarks, "
                f"{counts['user']} supplied by you.",
                None,
            ),
            ("", None),
            ("  deck       stated by the company, with the slide or page it appears on", None),
            ("  derived    computed from other real figures; the note says how", None),
            ("  benchmark  supplied by this tool because the deck did not state it", None),
            ("  user       your own override", None),
            ("", None),
            (
                "Benchmark lines marked 'planning convention' are midpoints in common "
                "use, not published figures. They are the lines worth replacing with "
                "your own numbers.",
                NOTE,
            ),
            ("", None),
            ("Calendar", SECTION),
            (assumptions.company.as_of_basis.value, None),
            ("", None),
            ("What this model says", SECTION),
        ]
        for label, value in self.model.break_even.items():
            lines.append((f"  {label}: {value}", None))

        if assumptions.grounding_warning_count:
            lines += [
                ("", None),
                ("Extraction warnings", SECTION),
                (
                    f"{assumptions.grounding_warning_count} quote(s) from the deck could "
                    f"not be confirmed on the page they cited. Check the DeckFacts tab.",
                    NOTE,
                ),
            ]

        lines += [
            ("", None),
            ("", None),
            (
                f"pitchdeck-cfo {MODEL_VERSION}  ·  This is a model built from figures "
                f"stated in an investor deck, filled out with labelled benchmarks. "
                f"It is not audited financial information and is not a forecast the "
                f"company has endorsed.",
                NOTE,
            ),
        ]

        for i, (text, font) in enumerate(lines, start=1):
            cell = ws.cell(row=i, column=1, value=text)
            cell.font = font or LABEL_FONT
            cell.alignment = Alignment(wrap_text=False, vertical="top")


def write(model: FinancialModel, path: Path) -> Path:
    """Render the model to a formula-driven workbook."""
    return WorkbookWriter(model).build(path)
