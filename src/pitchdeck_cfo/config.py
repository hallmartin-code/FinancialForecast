"""Runtime settings, bound from the environment and a .env file.

Every knob here is a *runtime* concern -- model, effort, paths, thresholds.
Business assumptions do not live here; they live in `assume/benchmarks.py`
where each value carries a citation.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Effort = Literal["low", "medium", "high", "xhigh", "max"]

# Minimum horizon that still produces a meaningful trajectory. Below three years
# the break-even and next-round analysis has nothing to say.
MIN_YEARS = 3
MAX_YEARS = 7


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PDCFO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Model -------------------------------------------------------------
    # Extraction is where fabrication would enter the pipeline, so this defaults
    # to the most accurate model available rather than the cheapest one.
    model: str = "claude-opus-5"
    effort: Effort = "high"
    max_tokens: int = 16_000

    # --- Modelling ---------------------------------------------------------
    years: int = 5
    # Base salary plus payroll tax, benefits, equipment and software seats.
    # 1.25-1.30 is the usual planning range; see assume/benchmarks.py for the source.
    loaded_cost_multiplier: float = 1.28

    # --- Provenance --------------------------------------------------------
    strict_coverage_threshold: float = Field(default=0.40, ge=0.0, le=1.0)

    # --- Email delivery ----------------------------------------------------
    # A completed build is emailed only when a key and a recipient are both present.
    # Nothing here is defaulted into being on: a service that silently forwards an
    # uploaded deck would be the kind of surprise this project exists to avoid.
    resend_api_key: str | None = None
    email_to: str = "Info@tencapital.group"
    email_from: str = "pitchdeck-cfo@tencapital.group"

    # --- Caching -----------------------------------------------------------
    cache_dir: Path = Path.home() / ".cache" / "pitchdeck-cfo"

    # --- Ingest ------------------------------------------------------------
    # A text PDF of any real deck clears this easily; a scan will not.
    min_text_chars: int = 200

    @field_validator("years")
    @classmethod
    def _years_in_range(cls, v: int) -> int:
        if not MIN_YEARS <= v <= MAX_YEARS:
            raise ValueError(f"years must be between {MIN_YEARS} and {MAX_YEARS}, got {v}")
        return v

    @property
    def email_enabled(self) -> bool:
        """Whether a finished build will actually be emailed.

        Read by the web page as well as the sender, so what an uploader is told
        matches what the service does rather than being written by hand.
        """
        return bool(self.resend_api_key and self.email_to)

    @property
    def has_credential(self) -> bool:
        """True when *any* credential source the SDK understands is present.

        An unset ANTHROPIC_API_KEY does not mean there is no credential -- the SDK
        also reads ANTHROPIC_AUTH_TOKEN and an `ant auth login` profile on disk.
        """
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return True
        profile_dir = Path.home() / ".config" / "anthropic"
        return profile_dir.is_dir() and any(profile_dir.iterdir())


# Read without the PDCFO_ prefix, because these are the names Resend's own docs and
# every hosting dashboard use. Prefixing them would be a small, permanent annoyance.
_UNPREFIXED = {
    "resend_api_key": "RESEND_API_KEY",
    "email_to": "EMAIL_TO",
    "email_from": "EMAIL_FROM",
}


def load_settings(**overrides: object) -> Settings:
    """Settings from env/.env, with explicit CLI flags taking precedence.

    `None` overrides are dropped so an unpassed typer option does not clobber an
    environment value with null.
    """
    clean = {k: v for k, v in overrides.items() if v is not None}
    for field, variable in _UNPREFIXED.items():
        value = os.environ.get(variable)
        if value and field not in clean:
            clean[field] = value
    return Settings(**clean)  # type: ignore[arg-type]
