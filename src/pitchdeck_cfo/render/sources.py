"""The Sources & Notes table -- provenance rendered as a document a human reads.

Every figure in the workbook has a source recorded on it. This turns that record
into the five-column table a diligence reader expects, grouped so the table stays
short: one row per *kind* of source rather than one row per value.

The `Notes` column always answers the same question -- what would have to happen for
this figure to be trusted more than it currently is.
"""

from __future__ import annotations

from collections import defaultdict

from pitchdeck_cfo.assume.overrides import resolved_names
from pitchdeck_cfo.model.build import FinancialModel

Row = tuple[str, str, str, str, str]


def source_rows(model: FinancialModel) -> list[Row]:
    """One row per source class, listing what in the model rests on it."""
    assumptions = model.assumptions
    resolved = resolved_names(assumptions)

    by_source: dict[str, list[str]] = defaultdict(list)
    citations: dict[str, set[str]] = defaultdict(set)
    for path, value in sorted(resolved.items()):
        label = path.replace(".", " ").replace("_", " ")
        by_source[value.source].append(label)
        if value.citation:
            citations[value.source].add(value.citation)

    rows: list[Row] = []
    deck_name = "The investor deck"

    if by_source["deck"]:
        pages = sorted(citations["deck"])[:8]
        rows.append(
            (
                deck_name,
                ", ".join(pages) if pages else "throughout",
                _uses(by_source["deck"]),
                "High",
                "Stated by the company. Every quote was checked against the page it "
                "cited"
                + (
                    f"; {assumptions.grounding_warning_count} could not be confirmed "
                    f"and should be verified against the source."
                    if assumptions.grounding_warning_count
                    else " and all were found."
                ),
            )
        )

    if assumptions.deck_revenue_projection:
        peak = max(value for _, value in assumptions.deck_revenue_projection)
        rows.append(
            (
                "The company's own financial projection",
                "The deck's financial table",
                f"Recorded as a claim only. Peak revenue ${peak:,.0f}. It is compared "
                f"with this model on the Summary sheet and drives nothing.",
                "Medium",
                "A forward projection is a claim, not a contracted figure. Ask what "
                "portion is contracted backlog versus pipeline.",
            )
        )

    if by_source["derived"]:
        rows.append(
            (
                "Derived from stated figures",
                "This workbook",
                _uses(by_source["derived"]),
                "Medium",
                "Arithmetic on figures the company gave. No more reliable than the "
                "inputs it rests on.",
            )
        )

    if by_source["benchmark"]:
        published = sorted({c for c in citations["benchmark"] if "convention" not in c})
        conventions = sorted({c for c in citations["benchmark"] if "convention" in c})
        if published:
            rows.append(
                (
                    "Published benchmarks",
                    "; ".join(published)[:400],
                    "Filled gaps the deck did not state.",
                    "Medium",
                    "Named, datable published figures. Still not this company's numbers.",
                )
            )
        if conventions:
            rows.append(
                (
                    "Planning conventions",
                    f"{len(conventions)} values, each labelled on Assumptions",
                    _uses(by_source["benchmark"]),
                    "Low/Medium",
                    "Midpoints in common use across venture finance, not published "
                    "figures. These are the yellow-filled rows, and they are the ones "
                    "to replace with company data during diligence.",
                )
            )

    if by_source["user"]:
        rows.append(
            (
                "Your own overrides",
                "assumptions.yaml",
                _uses(by_source["user"]),
                "As you state",
                "Supplied by whoever ran the model. Outranks everything else.",
            )
        )

    rows.append(
        (
            "Model structure and method",
            "This workbook",
            "Revenue build, hiring triggers, working capital, and the P&L and cash flow structure.",
            "Medium",
            "An annual restatement of a monthly engine. Break-even month, peak cash "
            "need and runway come from the monthly model and appear on the one-pager.",
        )
    )
    return rows


def _uses(labels: list[str], limit: int = 6) -> str:
    """What rests on a source, without listing forty paths."""
    shown = ", ".join(labels[:limit])
    extra = len(labels) - limit
    return f"{shown}{f', and {extra} more' if extra > 0 else ''}."
