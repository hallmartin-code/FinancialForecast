"""The workbook: structure, the colour convention, and -- the one that matters --
that the Excel formulas reproduce the Python model.

The model exists twice, once in `model/` and once as Excel formulas, and the value of
the workbook rests on the two agreeing. `TestExcelAgreesWithPython` compiles the
generated file with a spreadsheet engine and checks it.

Revenue, gross profit and EBITDA must agree to the dollar. Cash is allowed a few
percent, because working capital and the loss carryforward depend on the path within
a year and an annual grid cannot carry that. The tolerance is asserted rather than
assumed, so a regression that widens it fails here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook

from pitchdeck_cfo.model import build
from pitchdeck_cfo.render import style as S
from pitchdeck_cfo.render import workbook
from tests import model_factories as mf

ENGINES = ["saas", "hardware", "life_sciences"]

FULL = dict(
    cogs=mf.cogs(
        hosting_pct_of_revenue=mf.s(9.0),
        hosting_scale_exponent=mf.s(0.85),
        support_pct_of_revenue=mf.s(4.0),
        payment_processing_pct=mf.s(2.9),
        bom_pct_of_asp=mf.s(40.0),
        consumable_gross_margin_pct=mf.s(75.0),
        warranty_pct_of_revenue=mf.s(2.0),
    ),
    headcount=mf.headcount(
        starting={"Engineering": 6, "Sales": 2, "G&A": 1},
        loaded_multiplier=mf.s(1.28),
        annual_attrition_pct=mf.s(15.0),
        rep_quota_annual=mf.s(600_000),
        rep_ramp_months=mf.s(6.0),
        quota_attainment_pct=mf.s(75.0),
        accounts_per_csm=mf.s(40.0),
        engineers_per_product_line=mf.s(6.0),
        product_lines=mf.s(1.0),
        revenue_per_engineer=mf.s(400_000),
        sales_per_marketing_hire=mf.s(3.0),
        ftes_per_ga_hire=mf.s(12.0),
        scale_exponent=mf.s(0.75),
    ),
    opex=mf.opex(
        marketing_pct_of_new_revenue=mf.s(40.0),
        rd_tooling_per_engineer=mf.s(6_000),
        rent_per_fte=mf.s(6_000),
        software_per_fte=mf.s(3_600),
        ga_fixed_annual=mf.s(180_000),
    ),
    working_capital=mf.working_capital(
        dso_days=mf.s(45.0),
        dpo_days=mf.s(30.0),
        inventory_days=mf.s(20.0),
        capex_pct_of_revenue=mf.s(2.0),
        capex_depreciation_years=mf.s(3.0),
    ),
    financing=mf.financing(cash_on_hand=mf.s(1_000_000), raise_amount=mf.s(6_000_000)),
    tax=mf.tax(blended_rate_pct=mf.s(25.0)),
)


def _model(engine: str) -> Any:
    revenue = {
        "saas": lambda: mf.saas_revenue(
            new_logo_growth_monthly_pct=mf.s(4.0),
            growth_decay_annual_pct=mf.s(30.0),
            terminal_growth_monthly_pct=mf.s(1.0),
            logo_churn_annual_pct=mf.s(10.0),
            net_revenue_retention_pct=mf.s(112.0),
            annual_prepay_mix_pct=mf.s(50.0),
        ),
        "hardware": lambda: mf.hardware_revenue(
            unit_growth_monthly_pct=mf.s(5.0),
            growth_decay_annual_pct=mf.s(30.0),
            terminal_growth_monthly_pct=mf.s(1.0),
            direct_sales_mix_pct=mf.s(60.0),
            distributor_margin_pct=mf.s(25.0),
        ),
        "life_sciences": mf.life_sciences_revenue,
    }[engine]
    return build(mf.assumptions(engine, revenue=revenue(), **FULL))  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def saas_book(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return workbook.write(_model("saas"), tmp_path_factory.mktemp("wb") / "saas.xlsx")


def _rows(path: Path, sheet: str) -> dict[str, int]:
    """Data rows by label.

    Section bands share their label with the subtotal beneath them ("Total revenue"
    heads a section *and* names a line), so only rows carrying a value in the first
    year column count.
    """
    ws = load_workbook(path)[sheet]
    found: dict[str, int] = {}
    for r in range(1, ws.max_row + 1):
        label = ws.cell(row=r, column=1).value
        if isinstance(label, str) and ws.cell(row=r, column=2).value is not None:
            found.setdefault(label, r)
    return found


class TestStructure:
    def test_sheets_are_present_and_in_reading_order(self, saas_book: Path) -> None:
        # Conclusion first, then the drivers, then the audit trail.
        assert load_workbook(saas_book).sheetnames == list(workbook.SHEETS)

    def test_years_are_columns_not_months(self, saas_book: Path) -> None:
        # Sixty columns is not a document anyone reads.
        ws = load_workbook(saas_book)["P&L"]
        assert ws.cell(row=S.YEAR_ROW, column=2).value == 2026
        assert ws.cell(row=S.YEAR_ROW, column=6).value == 2030
        assert ws.cell(row=S.YEAR_ROW, column=7).value is None

    @pytest.mark.parametrize(
        "sheet", ["Assumptions", "Revenue Build", "Headcount", "P&L", "Cash Flow"]
    )
    def test_every_sheet_carries_the_same_chrome(self, saas_book: Path, sheet: str) -> None:
        ws = load_workbook(saas_book)[sheet]
        assert str(ws.cell(row=1, column=1).fill.fgColor.rgb).endswith(S.TITLE_BG)
        assert ws.cell(row=3, column=1).value == S.UNITS_NOTE
        assert ws.freeze_panes is not None

    def test_the_summary_states_provenance_and_the_disclaimer(self, saas_book: Path) -> None:
        ws = load_workbook(saas_book)["Summary"]
        text = "\n".join(str(c.value) for c in ws["A"] if c.value)
        assert "core inputs sourced from the deck" in text
        assert "Blue cells are inputs" in text
        assert "Not audited financial information" in text
        assert "annual restatement of a monthly model" in text

    def test_sources_and_notes_accounts_for_the_inputs(self, saas_book: Path) -> None:
        ws = load_workbook(saas_book)["Sources & Notes"]
        headers = [ws.cell(row=5, column=c).value for c in range(1, 6)]
        assert headers == ["Source", "Location", "Model use", "Confidence", "Notes"]
        assert ws.cell(row=6, column=1).value


class TestColourConvention:
    """Colour carries meaning here, so it has to be right."""

    def test_assumptions_are_blue_inputs(self, saas_book: Path) -> None:
        ws = load_workbook(saas_book)["Assumptions"]
        row = _rows(saas_book, "Assumptions")["A/R days"]
        cell = ws.cell(row=row, column=2)
        assert str(cell.font.color.rgb).endswith(S.BLUE)
        assert isinstance(cell.value, int | float)

    def test_cross_sheet_references_are_green(self, saas_book: Path) -> None:
        ws = load_workbook(saas_book)["Headcount"]
        row = _rows(saas_book, "Headcount")["Engineering"]
        cell = ws.cell(row=row, column=2)
        assert str(cell.font.color.rgb).endswith(S.GREEN)
        assert str(cell.value).startswith("=Assumptions!")

    def test_formulas_computed_here_are_black(self, saas_book: Path) -> None:
        ws = load_workbook(saas_book)["P&L"]
        row = _rows(saas_book, "P&L")["Gross profit"]
        cell = ws.cell(row=row, column=2)
        assert str(cell.font.color.rgb).endswith(S.BLACK)
        assert str(cell.value).startswith("=")

    def test_benchmark_inputs_are_flagged(self, tmp_path: Path) -> None:
        """A benchmark is the company's missing number, so it is marked for replacing."""
        model = build(
            mf.assumptions(
                "saas",
                working_capital=mf.working_capital(dso_days=mf.s(45.0, "benchmark")),
            )
        )
        path = workbook.write(model, tmp_path / "flagged.xlsx")
        ws = load_workbook(path)["Assumptions"]
        row = _rows(path, "Assumptions")["A/R days"]
        assert ws.cell(row=row, column=2).fill.fill_type == "solid"
        assert str(ws.cell(row=row, column=2).fill.fgColor.rgb).endswith(S.FLAG_FILL)

    def test_a_deck_sourced_input_is_not_flagged(self, tmp_path: Path) -> None:
        model = build(
            mf.assumptions(
                "saas",
                working_capital=mf.working_capital(dso_days=mf.s(45.0, "deck")),
            )
        )
        path = workbook.write(model, tmp_path / "clean.xlsx")
        ws = load_workbook(path)["Assumptions"]
        row = _rows(path, "Assumptions")["A/R days"]
        assert ws.cell(row=row, column=2).fill.fill_type != "solid"


class TestFormulasNotValues:
    @pytest.mark.parametrize(
        ("sheet", "label"),
        [
            ("Revenue Build", "Total revenue"),
            ("Headcount", "Total headcount"),
            ("P&L", "Gross profit"),
            ("P&L", "EBITDA"),
            ("P&L", "Net income"),
            ("Cash Flow", "Ending cash"),
            ("Summary", "Revenue"),
        ],
    )
    def test_key_lines_are_live_formulas(self, saas_book: Path, sheet: str, label: str) -> None:
        ws = load_workbook(saas_book)[sheet]
        row = _rows(saas_book, sheet)[label]
        for column in (2, 4, 6):
            value = ws.cell(row=row, column=column).value
            assert isinstance(value, str) and value.startswith("="), (
                f"{sheet}!{label} column {column} is a literal, not a formula"
            )

    def test_only_assumptions_holds_typed_numbers(self, saas_book: Path) -> None:
        """A hard-coded value elsewhere will not respond when an assumption changes."""
        wb = load_workbook(saas_book)
        for sheet in ("Revenue Build", "Headcount", "P&L", "Cash Flow"):
            ws = wb[sheet]
            for row in ws.iter_rows(min_row=S.YEAR_ROW + 1, min_col=2, max_col=6):
                for cell in row:
                    if cell.value is None:
                        continue
                    assert not isinstance(cell.value, int | float), (
                        f"{sheet}!{cell.coordinate} is a typed number, not a formula"
                    )

    def test_divisions_are_guarded(self, saas_book: Path) -> None:
        # A pre-revenue year divides by zero, and #DIV/0! reaching the Summary makes
        # the whole model look broken.
        ws = load_workbook(saas_book)["P&L"]
        row = _rows(saas_book, "P&L")["Gross margin"]
        assert "IFERROR" in str(ws.cell(row=row, column=2).value)

    def test_costs_are_negative_so_subtotals_add(self, saas_book: Path) -> None:
        ws = load_workbook(saas_book)["P&L"]
        rows = _rows(saas_book, "P&L")
        assert str(ws.cell(row=rows["Cost of revenue"], column=2).value).startswith("=-")
        gross = str(ws.cell(row=rows["Gross profit"], column=2).value)
        assert "+" in gross and "-" not in gross.replace("=", "")


class TestNumberFormats:
    def test_money_shows_red_parenthesised_negatives_and_a_dash_for_zero(
        self, saas_book: Path
    ) -> None:
        ws = load_workbook(saas_book)["P&L"]
        row = _rows(saas_book, "P&L")["EBITDA"]
        assert ws.cell(row=row, column=2).number_format == S.MONEY
        assert "[Red]" in S.MONEY
        assert S.MONEY.endswith("-")

    def test_percentages_use_a_percent_format(self, saas_book: Path) -> None:
        ws = load_workbook(saas_book)["P&L"]
        row = _rows(saas_book, "P&L")["Gross margin"]
        assert ws.cell(row=row, column=2).number_format == S.PERCENT


# --------------------------------------------------------------------------- #

formulas = pytest.importorskip("formulas", reason="spreadsheet engine not installed")


def _evaluate(path: Path) -> Any:
    import logging
    import warnings

    warnings.filterwarnings("ignore")
    logging.disable(logging.WARNING)
    solution = formulas.ExcelModel().loads(str(path.resolve())).finish().calculate()

    def value(sheet: str, cell: str) -> Any:
        import numpy as np

        # The engine uppercases sheet names in its keys.
        raw = np.asarray(solution[f"'[{path.name}]{sheet.upper()}'!{cell}"].value).ravel()[0]
        return raw if isinstance(raw, str) else float(raw)

    return value


COLUMNS = "BCDEF"

# Everything down to EBITDA is definitionally aggregable, so it must match to the
# dollar. Below that the path within a year matters, and the tolerance is pinned so a
# regression that widens it fails here rather than being discovered by a reader.
EXACT = {"Total revenue": "Revenue", "Gross profit": "Gross Profit", "EBITDA": "EBITDA"}
CASH_TOLERANCE = 0.05


@pytest.mark.slow
class TestExcelAgreesWithPython:
    @pytest.mark.parametrize("engine", ENGINES)
    @pytest.mark.parametrize("label", list(EXACT))
    def test_lines_above_ebitda_match_exactly(
        self, engine: str, label: str, tmp_path: Path
    ) -> None:
        model = _model(engine)
        path = workbook.write(model, tmp_path / f"{engine}.xlsx")
        value = _evaluate(path)
        row = _rows(path, "P&L")[label]
        for i, column in enumerate(COLUMNS):
            excel = value("P&L", f"{column}{row}") * S.THOUSANDS
            expected = model.pnl_annual[EXACT[label]][i]
            assert excel == pytest.approx(expected, abs=1.0), f"{engine} {label} Y{i + 1}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_ending_cash_is_within_the_restatement_tolerance(
        self, engine: str, tmp_path: Path
    ) -> None:
        model = _model(engine)
        path = workbook.write(model, tmp_path / f"{engine}.xlsx")
        value = _evaluate(path)
        row = _rows(path, "Cash Flow")["Ending cash"]
        for i, column in enumerate(COLUMNS):
            excel = value("Cash Flow", f"{column}{row}") * S.THOUSANDS
            expected = model.ending_cash[i]
            assert abs(excel - expected) <= max(CASH_TOLERANCE * abs(expected), 30_000), (
                f"{engine} ending cash Y{i + 1}: {excel:,.0f} vs {expected:,.0f}"
            )

    @pytest.mark.parametrize("engine", ENGINES)
    def test_no_cell_evaluates_to_an_error(self, engine: str, tmp_path: Path) -> None:
        """A #VALUE! or #NAME? anywhere means a formula this tool wrote is malformed."""
        model = _model(engine)
        path = workbook.write(model, tmp_path / f"{engine}.xlsx")
        value = _evaluate(path)
        wb = load_workbook(path)
        for sheet in ("Revenue Build", "Headcount", "P&L", "Cash Flow", "Summary"):
            ws = wb[sheet]
            for row in ws.iter_rows(min_row=S.YEAR_ROW + 1, min_col=2, max_col=6):
                for cell in row:
                    if not isinstance(cell.value, str) or not cell.value.startswith("="):
                        continue
                    result = value(sheet, cell.coordinate)
                    assert not (isinstance(result, str) and result.startswith("#")), (
                        f"{sheet}!{cell.coordinate} evaluated to {result}"
                    )

    def test_changing_an_assumption_recalculates_the_model(self, tmp_path: Path) -> None:
        """The whole point of the workbook: it is a live model, not a screenshot."""
        model = _model("saas")
        path = workbook.write(model, tmp_path / "before.xlsx")
        row = _rows(path, "P&L")["Total revenue"]
        before = _evaluate(path)("P&L", f"F{row}")

        wb = load_workbook(path)
        ws = wb["Assumptions"]
        target = _rows(path, "Assumptions")["Average paying customers"]
        for i in range(len(COLUMNS)):
            current = ws.cell(row=target, column=2 + i).value
            ws.cell(row=target, column=2 + i, value=float(current) * 2)
        edited = tmp_path / "after.xlsx"
        wb.save(edited)

        after = _evaluate(edited)("P&L", f"F{row}")
        assert after == pytest.approx(before * 2, rel=0.01)
