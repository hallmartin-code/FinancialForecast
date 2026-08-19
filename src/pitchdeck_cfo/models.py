"""Shared types. The provenance spine of the whole pipeline lives here.

The single design constraint this project is built around: **no number reaches an
output without declaring where it came from**. `Sourced[T]` is how that is enforced
mechanically rather than by discipline.
"""

from __future__ import annotations

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

T = TypeVar("T")

Provenance = Literal["deck", "derived", "benchmark", "user"]
Confidence = Literal["high", "medium", "low"]

# Resolution order when several sources offer the same input. A founder-stated
# figure beats a benchmark; an explicit user override beats everything, because
# the user is the one who has to defend the model.
PROVENANCE_PRECEDENCE: dict[Provenance, int] = {
    "benchmark": 0,
    "derived": 1,
    "deck": 2,
    "user": 3,
}

# What the one-pager prints next to a value so a reader can tell at a glance where
# it came from. Deck-sourced values are unmarked -- they are the default expectation.
PROVENANCE_MARK: dict[Provenance, str] = {
    "deck": "",
    "derived": "\u2020",  # dagger
    "benchmark": "\u2021",  # double dagger
    "user": "\u00a7",  # section sign
}


class Sourced(BaseModel, Generic[T]):
    """A value that knows its own origin.

    `citation` is mandatory for anything not derived: a deck value must name the
    slide or page, a benchmark must name its published source, a user override must
    name the file it came from. Enforced below, not merely documented.
    """

    model_config = ConfigDict(frozen=True)

    value: T
    source: Provenance
    citation: str | None = None
    confidence: Confidence = "medium"
    note: str | None = None

    @field_validator("citation")
    @classmethod
    def _citation_present_when_required(cls, v: str | None, info: object) -> str | None:
        # Validated against `source` in the model validator below; field order in
        # pydantic v2 means `source` may not be populated yet here.
        return v

    def model_post_init(self, __context: object) -> None:
        if self.source in ("deck", "benchmark", "user") and not self.citation:
            raise ValueError(
                f"a {self.source!r}-sourced value must carry a citation (got value={self.value!r})"
            )

    @property
    def mark(self) -> str:
        """Superscript marker for rendering."""
        return PROVENANCE_MARK[self.source]

    def beats(self, other: Sourced[T]) -> bool:
        return PROVENANCE_PRECEDENCE[self.source] > PROVENANCE_PRECEDENCE[other.source]

    def __str__(self) -> str:
        return f"{self.value}{self.mark}"


class DeckValue(Sourced[T], Generic[T]):
    """A `Sourced` that can only be deck-sourced.

    The extraction schema is built entirely from these. That makes it structurally
    impossible for the extraction pass to emit a benchmark or an inference -- the
    model would have to lie about the `source` field to do it, and the literal type
    rejects that at validation. Gap-filling happens later, in `assume/`, where it is
    labelled.
    """

    source: Literal["deck"] = "deck"


class BusinessModel(BaseModel):
    """How the company makes money, which selects the revenue engine."""

    model_config = ConfigDict(frozen=True)

    kind: Literal[
        "life_sciences",  # pre-revenue, milestone- and licensing-driven
        "saas",  # subscription cohort build
        "marketplace",  # GMV x take rate
        "transactional",  # active accounts x usage x unit price
        "hardware",  # units x ASP, BOM-driven COGS
        "services",
        "unknown",
    ]
    rationale: str


class CoverageReport(BaseModel):
    """How much of the model rests on the founder's own numbers.

    Printed by the CLI, stamped in the one-pager footer, written to the workbook
    README, and the gate that `--strict` checks.
    """

    model_config = ConfigDict(frozen=True)

    core_inputs: tuple[str, ...]
    by_source: dict[Provenance, tuple[str, ...]] = Field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.core_inputs)

    @property
    def from_deck(self) -> int:
        return len(self.by_source.get("deck", ()))

    @property
    def from_benchmark(self) -> int:
        return len(self.by_source.get("benchmark", ()))

    @property
    def from_derived(self) -> int:
        return len(self.by_source.get("derived", ()))

    @property
    def from_user(self) -> int:
        return len(self.by_source.get("user", ()))

    @property
    def deck_ratio(self) -> float:
        return self.from_deck / self.total if self.total else 0.0

    def summary_line(self) -> str:
        """The sentence that goes in the one-pager footer."""
        return (
            f"{self.from_deck} of {self.total} core inputs sourced from the deck; "
            f"{self.from_benchmark} from benchmarks, {self.from_derived} derived, "
            f"{self.from_user} user-supplied."
        )
