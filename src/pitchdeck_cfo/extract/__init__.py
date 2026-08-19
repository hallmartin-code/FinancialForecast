"""Extract stage: a `DeckDocument` becomes `DeckFacts` -- what the deck states, only.

Two passes. The profile pass runs first because its business-model classification is
context the financial pass needs to know which of its fields are even applicable.
Both are cached independently, so a prompt change to one does not invalidate the other.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pitchdeck_cfo.config import Settings
from pitchdeck_cfo.extract import grounding, prompts
from pitchdeck_cfo.extract.cache import ExtractionCache, cache_key, file_sha256
from pitchdeck_cfo.extract.client import ExtractionClient
from pitchdeck_cfo.extract.schema import (
    PROMPT_VERSION,
    CompanyProfile,
    DeckFacts,
    FinancialFacts,
)
from pitchdeck_cfo.ingest.base import DeckDocument

__all__ = [
    "PROMPT_VERSION",
    "CompanyProfile",
    "DeckFacts",
    "ExtractionCache",
    "ExtractionClient",
    "FinancialFacts",
    "extract_facts",
]

ProgressFn = Callable[[str], None]


def _noop(_: str) -> None:  # pragma: no cover - default progress sink
    return None


def default_cache(settings: Settings, *, enabled: bool = True) -> ExtractionCache:
    return ExtractionCache(settings.cache_dir.expanduser(), enabled=enabled)


def extract_facts(
    document: DeckDocument,
    *,
    settings: Settings,
    client: ExtractionClient | None = None,
    cache: ExtractionCache | None = None,
    progress: ProgressFn = _noop,
) -> DeckFacts:
    """Run both extraction passes against a deck and ground the results.

    The returned facts contain only what the deck states. Every value carries the
    page it came from and the quote that supports it, and any quote that could not be
    found in the deck is reported in `grounding_warnings` rather than quietly kept.
    """
    cache = cache if cache is not None else default_cache(settings)
    deck_hash = file_sha256(Path(document.path))
    deck_text = document.as_prompt_text()

    def run(
        pass_name: str,
        output_format: type[CompanyProfile] | type[FinancialFacts],
        system: str,
        user: str,
    ) -> CompanyProfile | FinancialFacts:
        key = cache_key(
            deck_sha256=deck_hash,
            prompt_version=PROMPT_VERSION,
            model=settings.model,
            pass_name=pass_name,
        )
        hit = cache.load(key, output_format)
        if hit is not None:
            progress(f"{pass_name}: cached")
            return hit

        nonlocal client
        if client is None:
            client = ExtractionClient(settings=settings)

        progress(f"{pass_name}: calling {settings.model}")
        result = client.extract(system=system, user=user, output_format=output_format)
        cache.store(key, result)
        return result

    profile = run(
        "profile",
        CompanyProfile,
        prompts.PROFILE_SYSTEM,
        prompts.PROFILE_USER.format(deck=deck_text),
    )
    assert isinstance(profile, CompanyProfile)

    business_model = (
        profile.business_model.value if profile.business_model is not None else "not classified"
    )
    financials = run(
        "financials",
        FinancialFacts,
        prompts.FINANCIALS_SYSTEM,
        prompts.FINANCIALS_USER.format(deck=deck_text, business_model=business_model),
    )
    assert isinstance(financials, FinancialFacts)

    warnings = grounding.check(profile, document) + grounding.check(financials, document)
    if warnings:
        progress(f"grounding: {len(warnings)} quote(s) could not be confirmed")

    return DeckFacts(
        profile=profile,
        financials=financials,
        model=settings.model,
        deck_sha256=deck_hash,
        grounding_warnings=warnings,
    )
