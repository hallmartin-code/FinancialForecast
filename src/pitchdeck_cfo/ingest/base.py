"""`DeckDocument` -- the normalised form every deck is reduced to.

A deck becomes a flat, ordered sequence of blocks. Each block carries the page or
slide it came from, which is what makes `citation="slide 7"` possible three stages
later. Nothing downstream ever reads a file again.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict

TextKind = Literal["title", "body", "notes"]
DeckKind = Literal["pdf", "pptx"]


class TextBlock(BaseModel):
    """A run of text from one page or slide."""

    model_config = ConfigDict(frozen=True)

    index: int
    """Position in the whole document. Preserves reading order across pages."""

    page: int
    """1-based page (PDF) or slide (PPTX) number."""

    kind: TextKind = "body"

    text: str


class TableBlock(BaseModel):
    """A table, kept as rows rather than flattened into prose.

    Decks put the numbers that matter in tables. Flattening them loses the column
    alignment that tells you which figure belongs to which year.
    """

    model_config = ConfigDict(frozen=True)

    index: int
    page: int
    kind: Literal["table"] = "table"
    rows: tuple[tuple[str, ...], ...]

    @property
    def text(self) -> str:
        """Pipe-delimited rendering. Column alignment survives into the prompt."""
        return "\n".join(" | ".join(cell for cell in row) for row in self.rows)

    @property
    def shape(self) -> tuple[int, int]:
        return len(self.rows), max((len(r) for r in self.rows), default=0)


Block: TypeAlias = TextBlock | TableBlock


class DeckDocument(BaseModel):
    """A deck, normalised. The only thing the extraction stage sees."""

    model_config = ConfigDict(frozen=True)

    path: Path
    kind: DeckKind
    page_count: int
    blocks: tuple[Block, ...]
    ocr_used: bool = False

    # --- provenance ------------------------------------------------------- #

    @property
    def page_word(self) -> str:
        """What a citation calls a page in this file type."""
        return "slide" if self.kind == "pptx" else "p."

    def citation(self, page: int) -> str:
        """The string that ends up in `Sourced.citation`."""
        return f"slide {page}" if self.kind == "pptx" else f"p. {page}"

    # --- views ------------------------------------------------------------ #

    @property
    def text_blocks(self) -> tuple[TextBlock, ...]:
        return tuple(b for b in self.blocks if isinstance(b, TextBlock))

    @property
    def tables(self) -> tuple[TableBlock, ...]:
        return tuple(b for b in self.blocks if isinstance(b, TableBlock))

    @property
    def notes(self) -> tuple[TextBlock, ...]:
        """Speaker notes. Founders put real numbers here that never reach a slide."""
        return tuple(b for b in self.text_blocks if b.kind == "notes")

    @property
    def char_count(self) -> int:
        """Total extracted characters. Drives the is-this-a-scan check."""
        return sum(len(b.text) for b in self.blocks)

    def blocks_on(self, page: int) -> tuple[Block, ...]:
        return tuple(b for b in self.blocks if b.page == page)

    # --- serialisation for the extraction prompt --------------------------- #

    def as_prompt_text(self) -> str:
        """The deck as the model will see it.

        Every block is prefixed with its citation so the model can quote a real
        location back to us instead of inventing one. Tables are fenced so the
        model does not mistake a pipe-delimited row for prose.
        """
        out: list[str] = []
        current_page = -1
        for block in self.blocks:
            if block.page != current_page:
                current_page = block.page
                out.append(f"\n=== {self.citation(block.page).upper()} ===")
            if isinstance(block, TableBlock):
                rows, cols = block.shape
                out.append(f"[table {rows}x{cols}]\n{block.text}")
            elif block.kind == "notes":
                out.append(f"[speaker notes] {block.text}")
            elif block.kind == "title":
                out.append(f"# {block.text}")
            else:
                out.append(block.text)
        return "\n".join(out).strip()

    def summary(self) -> str:
        """One line for the CLI progress output."""
        return (
            f"{self.page_word.rstrip('.')}s: {self.page_count} · "
            f"blocks: {len(self.blocks)} ({len(self.tables)} tables, "
            f"{len(self.notes)} notes) · {self.char_count:,} chars"
            + (" · OCR" if self.ocr_used else "")
        )
