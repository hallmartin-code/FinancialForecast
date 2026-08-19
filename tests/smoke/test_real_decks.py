"""End-to-end ingest against real decks that live outside the repo.

These are the 18.7 MB investor deck and the 50 MB scanned deck. They are too large to
commit, so every test here skips when the file is not on this machine. Marked `smoke`
and deselected in CI.
"""

from __future__ import annotations

import pytest

from tests.conftest import external_deck

pytestmark = pytest.mark.smoke


class TestRealInvestorDeck:
    def test_ingests_a_30_page_text_pdf(self) -> None:
        from pitchdeck_cfo.ingest import load_deck

        d = load_deck(external_deck("accubreath"))
        assert d.page_count == 30
        assert d.char_count > 15_000
        assert [b.index for b in d.blocks] == list(range(len(d.blocks)))

    def test_finds_the_founders_financial_projection_table(self) -> None:
        """The single most valuable object in a deck for this tool."""
        from pitchdeck_cfo.ingest import load_deck

        tables = load_deck(external_deck("accubreath")).tables
        assert len(tables) == 1, "layout ruling should not survive the density filter"

        table = tables[0]
        header = table.rows[0]
        assert header[0] == "$000s"
        assert header[1:] == ("2026", "2027", "2028", "2029", "2030")

        labels = {row[0] for row in table.rows}
        for line in ("Revenue", "COGS", "Gross Profit", "OpEx", "EBITDA", "Net Income"):
            assert line in labels, f"missing P&L line: {line}"

    def test_layout_mode_keeps_headings_off_the_following_sentence(self) -> None:
        # Default extraction yields "PROBLEM190,600 patients suffer..." as one run.
        from pitchdeck_cfo.ingest import load_deck

        text = load_deck(external_deck("accubreath")).as_prompt_text()
        assert "PROBLEM190,600" not in text


class TestRealScannedDeck:
    def test_large_scan_is_refused_with_the_ocr_remedy(self) -> None:
        from pitchdeck_cfo.errors import InsufficientTextError
        from pitchdeck_cfo.ingest import load_deck

        with pytest.raises(InsufficientTextError) as exc:
            load_deck(external_deck("scanned_large"))
        assert "--ocr" in exc.value.remedy
