"""PDF ingestion.

Text comes from pypdf, tables from pdfplumber. A PDF carries no reliable structural
semantics, so unlike the PPTX reader this one does not claim to know which line is a
title -- everything textual is `body`. Guessing would put an invented structure into
the record before the extraction pass has even run.

Table cell text usually also appears in the page text: pypdf cannot exclude the
regions pdfplumber found. That redundancy is deliberate -- the extraction pass reads
better seeing both the prose and the aligned table.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pdfplumber
from pypdf import PdfReader

from pitchdeck_cfo.ingest.base import Block, DeckDocument, TableBlock, TextBlock

# pdfplumber finds a "table" wherever a deck uses ruled lines for layout. Real tables
# are dense; layout artifacts are mostly empty cells. Measured against a real deck,
# genuine financial tables score ~0.8 density while layout artifacts score ~0.4.
MIN_TABLE_ROWS = 2
MIN_TABLE_COLS = 2
MIN_TABLE_DENSITY = 0.5


@contextmanager
def _quiet_pypdf() -> Iterator[None]:
    """Silence pypdf's per-object structural warnings.

    Real decks are exported by tools that emit slightly malformed cross-reference
    entries; pypdf recovers from all of them. The warnings are noise the user cannot
    act on, so they are suppressed here rather than shown as if they mattered.
    """
    logger = logging.getLogger("pypdf")
    previous = logger.level
    logger.setLevel(logging.ERROR)
    try:
        yield
    finally:
        logger.setLevel(previous)


def _paragraphs(page_text: str) -> list[str]:
    """Split a page into blocks on blank lines, keeping single newlines intact.

    Deck pages are mostly short lines. Splitting on every newline would shatter a
    wrapped sentence; splitting only on blank lines keeps related lines together.
    """
    chunks: list[str] = []
    buffer: list[str] = []
    for line in page_text.splitlines():
        if line.strip():
            buffer.append(line.strip())
        elif buffer:
            chunks.append("\n".join(buffer))
            buffer = []
    if buffer:
        chunks.append("\n".join(buffer))
    return chunks


def _is_real_table(rows: tuple[tuple[str, ...], ...]) -> bool:
    """Reject layout ruling that pdfplumber reported as a table."""
    n_rows = len(rows)
    n_cols = max((len(r) for r in rows), default=0)
    if n_rows < MIN_TABLE_ROWS or n_cols < MIN_TABLE_COLS:
        return False
    cells = n_rows * n_cols
    filled = sum(1 for row in rows for cell in row if cell)
    return bool(cells) and filled / cells >= MIN_TABLE_DENSITY


def _tables_by_page(path: Path) -> dict[int, list[tuple[tuple[str, ...], ...]]]:
    """Extract tables once, keyed by 1-based page number."""
    found: dict[int, list[tuple[tuple[str, ...], ...]]] = {}
    with pdfplumber.open(str(path)) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            tables = [
                rows
                for raw in page.extract_tables()
                # pdfplumber uses None for an empty cell.
                if _is_real_table(
                    rows := tuple(tuple((cell or "").strip() for cell in row) for row in raw)
                )
            ]
            if tables:
                found[page_no] = tables
    return found


def load_pdf(path: Path) -> DeckDocument:
    """Read a text-bearing .pdf into a `DeckDocument`.

    Makes no judgement about whether the result has enough text to be usable -- that
    check belongs to the caller, which knows whether --ocr was requested.
    """
    with _quiet_pypdf():
        reader = PdfReader(str(path))
        tables = _tables_by_page(path)
        blocks: list[Block] = []
        index = 0

        for page_no, page in enumerate(reader.pages, start=1):
            # "layout" mode preserves the visual line breaks and blank lines a deck
            # relies on. The default mode concatenates a slide heading onto the first
            # sentence of its body, which destroys the paragraph split below.
            text = page.extract_text(extraction_mode="layout") or ""
            for chunk in _paragraphs(text):
                blocks.append(TextBlock(index=index, page=page_no, kind="body", text=chunk))
                index += 1
            for rows in tables.get(page_no, []):
                blocks.append(TableBlock(index=index, page=page_no, rows=rows))
                index += 1

    return DeckDocument(
        path=path,
        kind="pdf",
        page_count=len(reader.pages),
        blocks=tuple(blocks),
    )
