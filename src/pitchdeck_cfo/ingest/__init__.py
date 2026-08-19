"""Ingest stage: a deck file becomes a `DeckDocument` and nothing reads the file again."""

from __future__ import annotations

from pathlib import Path

from pitchdeck_cfo.errors import (
    DeckNotFoundError,
    InsufficientTextError,
    UnsupportedFileTypeError,
)
from pitchdeck_cfo.ingest.base import Block, DeckDocument, TableBlock, TextBlock

__all__ = [
    "Block",
    "DeckDocument",
    "TableBlock",
    "TextBlock",
    "load_deck",
]

# A text PDF of any real deck clears this comfortably. Anything below it is a scan.
DEFAULT_MIN_TEXT_CHARS = 200


def load_deck(
    path: Path,
    *,
    ocr: bool = False,
    min_text_chars: int = DEFAULT_MIN_TEXT_CHARS,
) -> DeckDocument:
    """Read a deck into its normalised form.

    Raises `InsufficientTextError` when a PDF yields too little text to be anything
    but a scan, rather than handing an almost-empty document to the extraction pass
    and letting the model invent a company from nothing.
    """
    if not path.exists():
        raise DeckNotFoundError(path)

    suffix = path.suffix.lower()

    if suffix == ".pptx":
        if ocr:
            # PPTX text is already text. Silently ignoring the flag would be worse
            # than saying so, but it is not worth failing the run over.
            pass
        from pitchdeck_cfo.ingest.pptx import load_pptx

        return load_pptx(path)

    if suffix != ".pdf":
        raise UnsupportedFileTypeError(path)

    if ocr:
        from pitchdeck_cfo.ingest.ocr import load_pdf_ocr

        return load_pdf_ocr(path)

    from pitchdeck_cfo.ingest.pdf import load_pdf

    document = load_pdf(path)
    if document.char_count < min_text_chars:
        raise InsufficientTextError(path, document.char_count, min_text_chars)
    return document
