"""The grounding check -- what turns "cite your source" into something verifiable."""

from __future__ import annotations

from pathlib import Path

from pitchdeck_cfo.extract import grounding
from pitchdeck_cfo.extract.schema import Cited, LifeSciencesFacts, Milestone
from pitchdeck_cfo.ingest.base import DeckDocument, TableBlock, TextBlock


def _deck() -> DeckDocument:
    return DeckDocument(
        path=Path("d.pptx"),
        kind="pptx",
        page_count=3,
        blocks=(
            TextBlock(index=0, page=1, kind="title", text="Meridian Freight OS"),
            TextBlock(index=1, page=2, text="$2.4M ARR as of Q2 2026"),
            TextBlock(index=2, page=2, text="Net revenue retention 114%"),
            TableBlock(index=3, page=3, rows=(("Year", "Revenue"), ("2027", "10,620"))),
        ),
    )


def _cited(value: object, citation: str, quote: str) -> Cited[object]:
    return Cited[object](value=value, citation=citation, quote=quote, confidence="high")


class TestNormalise:
    def test_case_and_whitespace_are_irrelevant(self) -> None:
        assert grounding.normalise("  Net   Revenue\nRetention ") == grounding.normalise(
            "netrevenueretention"
        )

    def test_pdf_letter_spacing_survives(self) -> None:
        # A real deck extracts "70-year-old" as "70-ye a r-old". A check that tripped
        # over that would report correct extractions as fabrications.
        assert grounding.normalise("70-ye a r-old") == grounding.normalise("70-year-old")

    def test_smart_typography_is_folded(self) -> None:
        # Ambiguous characters are the subject under test, not a typo.
        assert grounding.normalise("1950’s") == grounding.normalise("1950's")  # noqa: RUF001
        assert grounding.normalise("A — B") == grounding.normalise("A - B")


class TestPageFromCitation:
    def test_reads_slide_and_page_forms(self) -> None:
        assert grounding.page_from_citation("slide 7") == 7
        assert grounding.page_from_citation("p. 12") == 12
        assert grounding.page_from_citation("page 3") == 3

    def test_returns_none_when_there_is_no_number(self) -> None:
        assert grounding.page_from_citation("the appendix") is None


class TestCheck:
    def test_a_true_citation_produces_no_warning(self) -> None:
        facts = LifeSciencesFacts(
            development_stage=_cited("x", "slide 2", "$2.4M ARR as of Q2 2026"),  # type: ignore[arg-type]
            regulatory_pathway=None,
            indication=None,
            next_milestone=None,
            non_dilutive_funding_usd=None,
            partnership_or_licensing_terms=None,
            trial_or_validation_data=[],
        )
        assert grounding.check(facts, _deck()) == ()

    def test_right_quote_wrong_page_is_flagged_as_such(self) -> None:
        facts = LifeSciencesFacts(
            development_stage=_cited("x", "slide 1", "$2.4M ARR as of Q2 2026"),  # type: ignore[arg-type]
            regulatory_pathway=None,
            indication=None,
            next_milestone=None,
            non_dilutive_funding_usd=None,
            partnership_or_licensing_terms=None,
            trial_or_validation_data=[],
        )
        (warning,) = grounding.check(facts, _deck())
        assert warning.reason == "quote_not_on_page"
        assert warning.field_path == "development_stage"

    def test_invented_quote_is_flagged_as_not_in_deck(self) -> None:
        """The failure this whole module exists to catch."""
        facts = LifeSciencesFacts(
            development_stage=_cited("x", "slide 2", "Series B raised at $80M pre-money"),  # type: ignore[arg-type]
            regulatory_pathway=None,
            indication=None,
            next_milestone=None,
            non_dilutive_funding_usd=None,
            partnership_or_licensing_terms=None,
            trial_or_validation_data=[],
        )
        (warning,) = grounding.check(facts, _deck())
        assert warning.reason == "quote_not_in_deck"

    def test_citation_to_a_page_that_does_not_exist(self) -> None:
        facts = LifeSciencesFacts(
            development_stage=_cited("x", "slide 99", "$2.4M ARR as of Q2 2026"),  # type: ignore[arg-type]
            regulatory_pathway=None,
            indication=None,
            next_milestone=None,
            non_dilutive_funding_usd=None,
            partnership_or_licensing_terms=None,
            trial_or_validation_data=[],
        )
        (warning,) = grounding.check(facts, _deck())
        assert warning.reason == "page_not_found"

    def test_table_text_counts_as_deck_text(self) -> None:
        facts = LifeSciencesFacts(
            development_stage=_cited("x", "slide 3", "2027 | 10,620"),  # type: ignore[arg-type]
            regulatory_pathway=None,
            indication=None,
            next_milestone=None,
            non_dilutive_funding_usd=None,
            partnership_or_licensing_terms=None,
            trial_or_validation_data=[],
        )
        assert grounding.check(facts, _deck()) == ()

    def test_short_quotes_are_not_checked(self) -> None:
        # "2026" appears on half the pages of any deck; flagging it would be noise.
        facts = LifeSciencesFacts(
            development_stage=_cited("x", "slide 1", "2026"),  # type: ignore[arg-type]
            regulatory_pathway=None,
            indication=None,
            next_milestone=None,
            non_dilutive_funding_usd=None,
            partnership_or_licensing_terms=None,
            trial_or_validation_data=[],
        )
        assert grounding.check(facts, _deck()) == ()


class TestWalksTheWholeTree:
    def test_finds_cited_values_nested_in_lists_and_submodels(self) -> None:
        facts = LifeSciencesFacts(
            development_stage=None,
            regulatory_pathway=None,
            indication=None,
            next_milestone=Milestone(
                description=_cited("m", "slide 9", "invented milestone text here"),  # type: ignore[arg-type]
                target_date=None,
                cost_usd=None,
            ),
            non_dilutive_funding_usd=None,
            partnership_or_licensing_terms=None,
            trial_or_validation_data=[
                _cited("t", "slide 9", "another invented claim entirely"),  # type: ignore[list-item]
            ],
        )
        paths = {w.field_path for w in grounding.check(facts, _deck())}
        assert paths == {
            "next_milestone.description",
            "trial_or_validation_data[0]",
        }
