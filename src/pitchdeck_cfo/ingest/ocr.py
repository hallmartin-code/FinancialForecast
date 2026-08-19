"""Scanned-deck fallback, behind --ocr.

OCR needs two binaries pip cannot install: tesseract (the engine) and poppler
(which pdf2image shells out to for rasterisation). Both are checked before any work
starts, and a missing one produces install instructions rather than a stack trace
from three libraries down.

Output from this module is materially less reliable than a text PDF -- column
alignment is lost, digits are misread. Callers mark `ocr_used=True` so downstream
confidence can be lowered accordingly.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from pitchdeck_cfo.errors import OCRUnavailableError
from pitchdeck_cfo.ingest.base import Block, DeckDocument, TextBlock

# 200 DPI is the point where tesseract's accuracy on 10pt deck body text stops
# improving materially; higher just costs time and memory.
DEFAULT_DPI = 200


def check_available() -> None:
    """Raise with install instructions unless both binaries are on PATH."""
    missing: list[str] = []
    if shutil.which("tesseract") is None:
        missing.append("the tesseract binary")
    if shutil.which("pdftoppm") is None:
        missing.append("poppler (pdftoppm)")
    try:
        import pdf2image  # noqa: F401
        import pytesseract  # noqa: F401
    except ImportError:
        missing.append("the [ocr] python extra")
    if missing:
        raise OCRUnavailableError(" and ".join(missing))


def load_pdf_ocr(path: Path, dpi: int = DEFAULT_DPI) -> DeckDocument:
    """Rasterise every page and OCR it into a `DeckDocument`.

    Produces text blocks only. Table structure does not survive rasterisation, and
    emitting a `TableBlock` from OCR output would assert a column alignment that was
    inferred rather than read.
    """
    check_available()

    from pdf2image import convert_from_path
    from pytesseract import image_to_string

    images = convert_from_path(str(path), dpi=dpi)
    blocks: list[Block] = []
    index = 0

    for page_no, image in enumerate(images, start=1):
        text = str(image_to_string(image)).strip()
        if not text:
            continue
        blocks.append(TextBlock(index=index, page=page_no, kind="body", text=text))
        index += 1

    return DeckDocument(
        path=path,
        kind="pdf",
        page_count=len(images),
        blocks=tuple(blocks),
        ocr_used=True,
    )
