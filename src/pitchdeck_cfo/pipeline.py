"""The whole pipeline, end to end.

One function so the CLI and the web app run identical code -- a web app that
reimplements the pipeline is a second implementation that will drift.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pitchdeck_cfo.assume import load as load_overrides
from pitchdeck_cfo.assume import resolve
from pitchdeck_cfo.assume.schema import ModelAssumptions
from pitchdeck_cfo.config import Settings
from pitchdeck_cfo.errors import InsufficientCoverageError
from pitchdeck_cfo.extract import DeckFacts, default_cache, extract_facts
from pitchdeck_cfo.ingest import load_deck
from pitchdeck_cfo.model import FinancialModel, build
from pitchdeck_cfo.render import onepager, workbook

ProgressFn = Callable[[str], None]


def _noop(_: str) -> None:  # pragma: no cover - default progress sink
    return None


def slug(name: str) -> str:
    """A filename-safe company name, so artifacts are identifiable on disk."""
    cleaned = re.sub(r"[^0-9A-Za-z]+", "_", name).strip("_")
    return cleaned or "company"


@dataclass(frozen=True)
class BuildResult:
    facts: DeckFacts
    assumptions: ModelAssumptions
    model: FinancialModel
    onepager_path: Path
    workbook_path: Path

    @property
    def coverage_line(self) -> str:
        return self.assumptions.coverage.summary_line()


def run(
    deck: Path,
    out_dir: Path,
    *,
    settings: Settings,
    assumptions_file: Path | None = None,
    strict: bool = False,
    ocr: bool = False,
    use_cache: bool = True,
    progress: ProgressFn = _noop,
) -> BuildResult:
    """Deck in, two artifacts out.

    `--strict` is checked after resolution and before rendering: the point is to
    refuse to *emit* a confident-looking model, so it fails before writing files
    rather than after.
    """
    progress("reading deck")
    document = load_deck(deck, ocr=ocr, min_text_chars=settings.min_text_chars)

    progress("extracting what the deck states")
    facts = extract_facts(
        document,
        settings=settings,
        cache=default_cache(settings, enabled=use_cache),
        progress=progress,
    )

    progress("resolving assumptions")
    overrides = load_overrides(assumptions_file) if assumptions_file else {}
    assumptions = resolve(facts, horizon_years=settings.years, overrides=overrides)

    coverage = assumptions.coverage
    if strict and coverage.deck_ratio < settings.strict_coverage_threshold:
        raise InsufficientCoverageError(
            coverage.from_deck, coverage.total, settings.strict_coverage_threshold
        )

    progress("building the model")
    model = build(assumptions)

    name = slug(assumptions.company.name)
    progress("writing the workbook")
    workbook_path = workbook.write(model, out_dir / f"{name}_financial_model.xlsx")
    progress("writing the one-pager")
    onepager_path = onepager.write(model, out_dir / f"{name}_financial_onepager.pdf")

    return BuildResult(
        facts=facts,
        assumptions=assumptions,
        model=model,
        onepager_path=onepager_path,
        workbook_path=workbook_path,
    )
