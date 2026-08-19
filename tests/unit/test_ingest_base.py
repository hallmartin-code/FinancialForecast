"""`DeckDocument` -- the normalised form and its provenance behaviour."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from pitchdeck_cfo.ingest.base import DeckDocument, TableBlock, TextBlock


def _doc(kind: str = "pptx", blocks: tuple[object, ...] = ()) -> DeckDocument:
    return DeckDocument(
        path=Path("x.pptx"),
        kind=kind,  # type: ignore[arg-type]
        page_count=3,
        blocks=blocks,  # type: ignore[arg-type]
    )


class TestCitation:
    def test_pptx_cites_slides(self) -> None:
        assert _doc("pptx").citation(7) == "slide 7"

    def test_pdf_cites_pages(self) -> None:
        assert _doc("pdf").citation(12) == "p. 12"

    def test_citation_is_what_sourced_will_carry(self) -> None:
        # This string is what ends up in Sourced.citation, so its shape is a contract.
        from pitchdeck_cfo.models import Sourced

        doc = _doc("pdf")
        v = Sourced[int](value=1, source="deck", citation=doc.citation(4))
        assert v.citation == "p. 4"


class TestTableBlock:
    def _table(self) -> TableBlock:
        return TableBlock(
            index=0,
            page=2,
            rows=(("$000s", "2026", "2027"), ("Revenue", "-", "2,430")),
        )

    def test_shape(self) -> None:
        assert self._table().shape == (2, 3)

    def test_text_preserves_column_alignment(self) -> None:
        # Flattening a table into prose loses which figure belongs to which year.
        assert self._table().text == "$000s | 2026 | 2027\nRevenue | - | 2,430"

    def test_ragged_rows_do_not_crash_shape(self) -> None:
        t = TableBlock(index=0, page=1, rows=(("a",), ("b", "c", "d")))
        assert t.shape == (2, 3)


class TestViews:
    def _mixed(self) -> DeckDocument:
        return _doc(
            "pptx",
            (
                TextBlock(index=0, page=1, kind="title", text="Acme"),
                TextBlock(index=1, page=1, kind="body", text="We sell things"),
                TextBlock(index=2, page=1, kind="notes", text="ARR is really 2.1M"),
                TableBlock(index=3, page=2, rows=(("a", "b"), ("1", "2"))),
            ),
        )

    def test_partitions_are_complete_and_disjoint(self) -> None:
        d = self._mixed()
        assert len(d.text_blocks) + len(d.tables) == len(d.blocks)

    def test_notes_are_addressable(self) -> None:
        # Founders hide real numbers in speaker notes; they must be reachable.
        assert [b.text for b in self._mixed().notes] == ["ARR is really 2.1M"]

    def test_char_count_includes_table_text(self) -> None:
        d = self._mixed()
        assert d.char_count == sum(len(b.text) for b in d.blocks)

    def test_blocks_on_page(self) -> None:
        assert len(self._mixed().blocks_on(1)) == 3
        assert len(self._mixed().blocks_on(99)) == 0


class TestPromptSerialisation:
    def test_every_page_boundary_is_labelled(self) -> None:
        # The model can only cite a real location if it was shown one.
        d = _doc(
            "pptx",
            (
                TextBlock(index=0, page=1, text="one"),
                TextBlock(index=1, page=2, text="two"),
            ),
        )
        text = d.as_prompt_text()
        assert "=== SLIDE 1 ===" in text
        assert "=== SLIDE 2 ===" in text

    def test_block_types_are_distinguishable_in_the_prompt(self) -> None:
        d = _doc(
            "pptx",
            (
                TextBlock(index=0, page=1, kind="title", text="Acme"),
                TextBlock(index=1, page=1, kind="notes", text="secret"),
                TableBlock(index=2, page=1, rows=(("a", "b"), ("1", "2"))),
            ),
        )
        text = d.as_prompt_text()
        assert "# Acme" in text
        assert "[speaker notes] secret" in text
        assert "[table 2x2]" in text

    def test_page_header_emitted_once_per_page_not_once_per_block(self) -> None:
        d = _doc(
            "pdf",
            (
                TextBlock(index=0, page=1, text="a"),
                TextBlock(index=1, page=1, text="b"),
            ),
        )
        assert d.as_prompt_text().count("=== P. 1 ===") == 1

    def test_empty_document_serialises_to_empty_string(self) -> None:
        assert _doc("pdf", ()).as_prompt_text() == ""


class TestImmutability:
    def test_blocks_cannot_be_mutated_after_ingest(self) -> None:
        b = TextBlock(index=0, page=1, text="x")
        with pytest.raises(ValidationError):
            b.text = "y"  # type: ignore[misc]
