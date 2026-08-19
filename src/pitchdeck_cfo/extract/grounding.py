"""Verify that every extracted value's quote actually appears where it was cited.

A schema can force the model to *supply* a citation. It cannot force the citation to
be true. This module closes that gap: it walks the returned facts, and for each
`Cited` value checks the verbatim quote against the real text of the page it names.

Three failure modes, in increasing severity:

- `quote_not_on_page` -- the text exists in the deck but on a different page. The
  value is probably right and the citation wrong.
- `page_not_found`   -- the cited page does not exist in this deck.
- `quote_not_in_deck` -- the text appears nowhere. The value was invented.

Nothing is silently dropped. Warnings travel with `DeckFacts` into the assumption
engine, which lowers confidence and reports them.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from pitchdeck_cfo.extract.schema import Evidence, GroundingReason, GroundingWarning
from pitchdeck_cfo.ingest.base import DeckDocument

# A quote shorter than this is too generic for containment to mean anything -- "2026"
# appears on half the pages of any deck. Short quotes are accepted without check
# rather than producing noise that trains the reader to ignore warnings.
MIN_CHECKABLE_QUOTE = 12

_PAGE_NUMBER = re.compile(r"(\d+)")

# Typography the extraction round-trips through: smart quotes, dashes and ligature
# spacing all differ between what the deck renders and what the model echoes back.
_TRANSLATIONS = str.maketrans(
    {
        "‘": "'",  # noqa: RUF001
        "’": "'",  # noqa: RUF001
        "“": '"',
        "”": '"',
        "–": "-",  # noqa: RUF001
        "—": "-",
        "−": "-",  # noqa: RUF001
        " ": " ",  # noqa: RUF001
    }
)


def normalise(text: str) -> str:
    """Reduce text to a form where only the characters that carry meaning remain.

    All whitespace is removed, not merely collapsed. PDF layout extraction pads and
    splits words unpredictably -- a real deck yields "70-ye a r-old" for "70-year-old"
    -- and a quote check that trips over that would flag correct extractions as
    fabrications.
    """
    return "".join(text.translate(_TRANSLATIONS).lower().split())


def page_from_citation(citation: str) -> int | None:
    """Pull the page or slide number out of 'slide 7' / 'p. 12' / 'page 3'."""
    match = _PAGE_NUMBER.search(citation)
    return int(match.group(1)) if match else None


def _page_text(document: DeckDocument) -> dict[int, str]:
    pages: dict[int, str] = {}
    for block in document.blocks:
        pages[block.page] = pages.get(block.page, "") + normalise(block.text)
    return pages


def _iter_evidence(node: Any, path: str = "") -> list[tuple[str, Evidence]]:
    """Walk a model tree and yield every `Evidence` with its dotted field path."""
    found: list[tuple[str, Evidence]] = []

    if isinstance(node, Evidence):
        found.append((path or "<root>", node))
        return found

    if isinstance(node, BaseModel):
        for name in type(node).model_fields:
            found.extend(_iter_evidence(getattr(node, name), f"{path}.{name}" if path else name))
        return found

    if isinstance(node, list | tuple):
        for i, item in enumerate(node):
            # A metric names itself, which reads far better than "stated[7]".
            label = getattr(item, "metric", None) or i
            found.extend(_iter_evidence(item, f"{path}[{label}]"))

    return found


def _classify(
    quote: str, citation: str, pages: dict[int, str], whole_deck: str
) -> GroundingReason | None:
    page = page_from_citation(citation)
    needle = normalise(quote)

    if page is None or page not in pages:
        # Still worth distinguishing an invented page from an invented quote.
        return "page_not_found" if needle in whole_deck else "quote_not_in_deck"
    if needle in pages[page]:
        return None
    return "quote_not_on_page" if needle in whole_deck else "quote_not_in_deck"


def check(model: BaseModel, document: DeckDocument) -> tuple[GroundingWarning, ...]:
    """Return one warning per value whose quote could not be confirmed."""
    pages = _page_text(document)
    whole_deck = "".join(pages.values())

    warnings: list[GroundingWarning] = []
    for path, cited in _iter_evidence(model):
        if len(normalise(cited.quote)) < MIN_CHECKABLE_QUOTE:
            continue
        reason = _classify(cited.quote, cited.citation, pages, whole_deck)
        if reason is not None:
            warnings.append(
                GroundingWarning(
                    field_path=path,
                    citation=cited.citation,
                    quote=cited.quote,
                    reason=reason,
                )
            )
    return tuple(warnings)
