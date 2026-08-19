"""Settings binding and the guardrails on the horizon."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pitchdeck_cfo.config import MAX_YEARS, MIN_YEARS, load_settings


class TestDefaults:
    def test_extraction_defaults_to_the_accurate_model(self) -> None:
        # Fabrication is this tool's worst failure mode, so extraction does not
        # default to the cheap model.
        assert load_settings().model == "claude-opus-5"

    def test_default_horizon_is_five_years(self) -> None:
        assert load_settings().years == 5


class TestOverridePrecedence:
    def test_explicit_argument_wins_over_environment(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setenv("PDCFO_MODEL", "claude-sonnet-5")
        assert load_settings(model="claude-haiku-4-5").model == "claude-haiku-4-5"

    def test_none_does_not_clobber_environment(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setenv("PDCFO_MODEL", "claude-sonnet-5")
        assert load_settings(model=None).model == "claude-sonnet-5"


class TestHorizonBounds:
    @pytest.mark.parametrize("years", [MIN_YEARS, 5, MAX_YEARS])
    def test_accepts_supported_horizons(self, years: int) -> None:
        assert load_settings(years=years).years == years

    @pytest.mark.parametrize("years", [0, 2, 8, 30])
    def test_rejects_horizons_that_cannot_show_a_trajectory(self, years: int) -> None:
        with pytest.raises(ValidationError):
            load_settings(years=years)


class TestCoverageThreshold:
    def test_must_be_a_fraction(self) -> None:
        with pytest.raises(ValidationError):
            load_settings(strict_coverage_threshold=1.5)
