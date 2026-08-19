"""The provenance spine. If these break, the tool can fabricate numbers."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pitchdeck_cfo.models import CoverageReport, DeckValue, Sourced


class TestSourcedCitationRule:
    @pytest.mark.parametrize("source", ["deck", "benchmark", "user"])
    def test_uncited_value_is_rejected(self, source: str) -> None:
        with pytest.raises(ValidationError, match="must carry a citation"):
            Sourced[float](value=1.0, source=source)  # type: ignore[arg-type]

    def test_derived_needs_no_citation(self) -> None:
        # A derived value's provenance is the arithmetic itself, which the workbook shows.
        assert Sourced[float](value=1.0, source="derived").citation is None

    def test_cited_value_is_accepted(self) -> None:
        v = Sourced[float](value=3.2e6, source="deck", citation="slide 5")
        assert v.value == 3.2e6
        assert v.citation == "slide 5"


class TestPrecedence:
    def test_user_beats_deck_beats_derived_beats_benchmark(self) -> None:
        user = Sourced[int](value=1, source="user", citation="assumptions.yaml")
        deck = Sourced[int](value=2, source="deck", citation="slide 3")
        derived = Sourced[int](value=3, source="derived")
        bench = Sourced[int](value=4, source="benchmark", citation="SaaS Capital 2024")

        assert user.beats(deck)
        assert deck.beats(derived)
        assert derived.beats(bench)
        assert not bench.beats(derived)

    def test_beats_is_strict(self) -> None:
        a = Sourced[int](value=1, source="deck", citation="slide 1")
        b = Sourced[int](value=2, source="deck", citation="slide 2")
        assert not a.beats(b)


class TestDeckValue:
    def test_source_defaults_to_deck(self) -> None:
        assert DeckValue[int](value=7, citation="slide 2").source == "deck"

    def test_cannot_be_anything_but_deck(self) -> None:
        # This is what stops the extraction pass emitting a benchmark or an inference.
        with pytest.raises(ValidationError):
            DeckValue[int](value=7, source="benchmark", citation="x")  # type: ignore[arg-type]


class TestMarks:
    def test_deck_values_are_unmarked_and_others_are_not(self) -> None:
        assert Sourced[int](value=1, source="deck", citation="s1").mark == ""
        assert Sourced[int](value=1, source="derived").mark != ""
        assert Sourced[int](value=1, source="benchmark", citation="x").mark != ""

    def test_marks_are_distinct(self) -> None:
        marks = {
            Sourced[int](value=1, source="derived").mark,
            Sourced[int](value=1, source="benchmark", citation="x").mark,
            Sourced[int](value=1, source="user", citation="y").mark,
        }
        assert len(marks) == 3


class TestCoverageReport:
    def _report(self) -> CoverageReport:
        return CoverageReport(
            core_inputs=("arr", "growth", "gross_margin", "churn", "cac"),
            by_source={
                "deck": ("arr", "growth", "gross_margin"),
                "benchmark": ("cac",),
                "derived": ("churn",),
            },
        )

    def test_counts(self) -> None:
        r = self._report()
        assert (r.total, r.from_deck, r.from_benchmark, r.from_derived, r.from_user) == (
            5,
            3,
            1,
            1,
            0,
        )

    def test_deck_ratio(self) -> None:
        assert self._report().deck_ratio == pytest.approx(0.6)

    def test_empty_report_does_not_divide_by_zero(self) -> None:
        assert CoverageReport(core_inputs=()).deck_ratio == 0.0

    def test_summary_line_states_every_bucket(self) -> None:
        line = self._report().summary_line()
        assert "3 of 5 core inputs sourced from the deck" in line
        assert "1 from benchmarks" in line
