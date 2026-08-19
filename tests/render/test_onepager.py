"""The one-pager. The hard constraint is one page; the rest is legibility."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from pitchdeck_cfo.model import build
from pitchdeck_cfo.render import charts, onepager
from pitchdeck_cfo.render.onepager import _money, _scale_for, _standalone_money
from tests import model_factories as mf
from tests.render.test_workbook import ENGINES, _model


@pytest.fixture(scope="module")
def saas_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("pdf") / "saas.pdf"
    return onepager.write(_model("saas"), out)


class TestExactlyOnePage:
    """Nothing may spill. A two-page one-pager is a different document."""

    @pytest.mark.parametrize("engine", ENGINES)
    def test_every_engine_fits_on_one_page(self, engine: str, tmp_path: Path) -> None:
        path = onepager.write(_model(engine), tmp_path / f"{engine}.pdf")
        assert len(PdfReader(path).pages) == 1

    def test_us_letter_portrait(self, saas_pdf: Path) -> None:
        box = PdfReader(saas_pdf).pages[0].mediabox
        assert (round(float(box.width)), round(float(box.height))) == (612, 792)

    def test_a_very_long_company_name_does_not_push_to_two_pages(self, tmp_path: Path) -> None:
        model = build(
            mf.assumptions(
                "saas",
                company=mf.company(
                    "saas",
                    name="A Company With An Unreasonably Long Registered Legal Name Limited",
                    sector="An equally long sector description that keeps going and going",
                ),
            )
        )
        assert len(PdfReader(onepager.write(model, tmp_path / "long.pdf")).pages) == 1

    def test_a_seven_year_horizon_still_fits(self, tmp_path: Path) -> None:
        model = build(mf.assumptions("saas", company=mf.company("saas", horizon_years=7)))
        assert len(PdfReader(onepager.write(model, tmp_path / "seven.pdf")).pages) == 1

    def test_a_pre_revenue_company_renders(self, tmp_path: Path) -> None:
        # Every ratio is undefined here; nothing may divide by zero or print inf.
        model = build(mf.assumptions("life_sciences"))
        path = onepager.write(model, tmp_path / "bare.pdf")
        assert len(PdfReader(path).pages) == 1
        # A standalone "inf"/"nan" would mean a ratio divided by zero reached the
        # page. Matched on word boundaries, since "Infra scale exponent" is fine.
        import re

        assert not re.search(r"(inf|nan|-inf)", _text(path), re.IGNORECASE)


def _text(path: Path) -> str:
    return PdfReader(path).pages[0].extract_text() or ""


class TestContent:
    def test_the_footer_states_coverage_and_the_disclaimer(self, saas_pdf: Path) -> None:
        text = _text(saas_pdf)
        assert "core inputs sourced from the deck" in text
        assert "Not audited financial information" in text

    def test_the_provenance_legend_is_present(self, saas_pdf: Path) -> None:
        text = _text(saas_pdf)
        assert "benchmark" in text
        assert "stated in the deck" in text

    def test_the_summary_table_carries_every_required_line(self, saas_pdf: Path) -> None:
        text = _text(saas_pdf)
        for line in (
            "Revenue",
            "YoY growth",
            "COGS",
            "Gross profit",
            "Gross margin",
            "R&D",
            "S&M",
            "G&A",
            "Total opex",
            "EBITDA",
            "Ending headcount",
            "Ending cash",
        ):
            assert line in text, f"summary table is missing {line}"

    def test_unit_economics_are_shown(self, saas_pdf: Path) -> None:
        text = _text(saas_pdf)
        assert "UNIT ECONOMICS" in text
        assert "Rule of 40" in text

    def test_negatives_render_in_parentheses(self, saas_pdf: Path) -> None:
        assert "(" in _text(saas_pdf)


class TestDeckVersusModel:
    """A partner reading a model far below the deck's own claim needs it on the page."""

    def _with_claim(self, peak: float) -> Any:
        assumptions = mf.assumptions("saas")
        return build(assumptions.model_copy(update={"deck_revenue_projection": (("2030", peak),)}))

    def test_a_large_gap_is_stated(self, tmp_path: Path) -> None:
        model = self._with_claim(model_peak := 500_000_000.0)
        _ = model_peak
        text = _text(onepager.write(model, tmp_path / "gap.pdf"))
        assert "DECK VS MODEL" in text
        assert "above this model" in text

    def test_a_close_projection_is_not_flagged(self, tmp_path: Path) -> None:
        base = build(mf.assumptions("saas"))
        model = self._with_claim(base.final_year_revenue * 1.05)
        assert "DECK VS MODEL" not in _text(onepager.write(model, tmp_path / "ok.pdf"))

    def test_no_projection_means_no_note(self, tmp_path: Path) -> None:
        model = build(mf.assumptions("saas"))
        assert "DECK VS MODEL" not in _text(onepager.write(model, tmp_path / "none.pdf"))


class TestNumberFormatting:
    def test_page_scale_stays_in_thousands_until_the_numbers_are_large(self) -> None:
        # A $6M company in millions renders as "0, 1, 1, 1, 2" -- useless.
        assert _scale_for([6_000_000.0]) == (1_000.0, "$K")
        assert _scale_for([42_000_000.0]) == (1_000_000.0, "$M")

    def test_millions_keep_a_decimal(self) -> None:
        assert _money(1_250_000, 1_000_000) == "1.2"

    def test_negatives_use_parentheses_not_a_minus(self) -> None:
        assert _money(-1_628_000, 1_000) == "(1,628)"

    def test_a_standalone_figure_picks_its_own_scale(self) -> None:
        # An ACV of $64,800 against a $M page must not render as "0.06M".
        assert _standalone_money(64_800) == "$65K"
        assert _standalone_money(4_000) == "$4,000"
        assert _standalone_money(2_000_000) == "$2.0M"
        assert _standalone_money(30) == "$30"


class TestCharts:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_both_charts_render_for_every_engine(self, engine: str) -> None:
        model = _model(engine)
        for png in (charts.revenue_vs_cost(model), charts.cash_balance(model)):
            assert png.startswith(b"\x89PNG")
            assert len(png) > 5_000

    def test_tick_decimals_avoid_duplicate_labels(self) -> None:
        # A fixed format produced axes reading "0, -0, -0, -1, -1".
        fine = charts._tick_formatter(0.5)
        coarse = charts._tick_formatter(50.0)
        assert fine(0.25, 0) != fine(0.5, 0)
        assert coarse(10.0, 0) == "10"

    def test_annotations_use_their_own_scale(self) -> None:
        assert charts._compact(2_000_000) == "$2.0M"
        # The sign belongs outside the currency symbol; "$-6.4M" is not money.
        assert charts._compact(-6_385_000) == "-$6.4M"
        assert _standalone_money(-6_385_000) == "-$6.4M"
