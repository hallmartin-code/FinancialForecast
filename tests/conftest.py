"""Shared fixtures. No test in this suite makes a network call."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE_DECKS = Path(__file__).parent / "fixtures" / "decks"

# Real decks that live outside the repo because of their size. Tests that use these
# are marked `smoke` and skip when the file is absent, so a clone still runs green.
EXTERNAL_DECKS = {
    "accubreath": Path(__file__).parents[2] / "pitchlens" / "samples" / "AccuBreath_Deck.pdf",
    "scanned_large": Path(__file__).parents[2] / "FounderTeamAnalysis" / "samples" / "scanned.pdf",
}


@pytest.fixture
def saas_deck() -> Path:
    """Meridian Freight OS -- a well-specified SaaS deck: ARR, NRR, churn, ACV, GM."""
    return FIXTURE_DECKS / "saas_meridian.pptx"


@pytest.fixture
def lifesci_deck() -> Path:
    """Helion Bio -- sparse pre-revenue oncology seed. Almost no financial data."""
    return FIXTURE_DECKS / "lifesci_helion.pptx"


@pytest.fixture
def hybrid_deck() -> Path:
    """Nimbus Freight -- subscription plus a take rate; exercises model classification."""
    return FIXTURE_DECKS / "hybrid_nimbus.pptx"


@pytest.fixture
def scanned_deck() -> Path:
    """An image-only PDF. Must trigger InsufficientTextError, not a silent empty parse."""
    return FIXTURE_DECKS / "scanned.pdf"


def external_deck(name: str) -> Path:
    """Path to a large real deck, or skip the test when it is not on this machine."""
    path = EXTERNAL_DECKS[name]
    if not path.exists():
        pytest.skip(f"external deck not present: {path}")
    return path
