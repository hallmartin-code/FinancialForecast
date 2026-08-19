"""The Anthropic client wrapper.

Owns exactly three concerns: making the structured-output call, retrying once with
the validation error fed back, and accounting for tokens. It knows nothing about what
is being extracted -- callers supply the model type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from pitchdeck_cfo.config import Settings
from pitchdeck_cfo.errors import ExtractionValidationError, MissingCredentialError
from pitchdeck_cfo.extract.prompts import RETRY_USER

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Sequence

M = TypeVar("M", bound=BaseModel)


@dataclass
class Usage:
    """Token accounting across a run, for --verbose and cost sanity."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    calls: int = 0
    retries: int = 0

    def add(self, usage: Any) -> None:
        self.calls += 1
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0

    def summary(self) -> str:
        return (
            f"{self.calls} call(s), {self.retries} retr{'y' if self.retries == 1 else 'ies'} · "
            f"in {self.input_tokens:,} (cached {self.cache_read_tokens:,}) · "
            f"out {self.output_tokens:,} tokens"
        )


@dataclass
class ExtractionClient:
    """A thin, testable wrapper. Tests substitute `_client` rather than the network."""

    settings: Settings
    _client: Any = None
    usage: Usage = field(default_factory=Usage)

    def __post_init__(self) -> None:
        if self._client is None:
            if not self.settings.has_credential:
                raise MissingCredentialError()
            # Zero-arg construction resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN
            # or an `ant auth login` profile, in that order.
            self._client = anthropic.Anthropic()

    def _call(self, *, system: str, messages: Sequence[Any], output_format: type[M]) -> Any:
        return self._client.messages.parse(
            model=self.settings.model,
            max_tokens=self.settings.max_tokens,
            # A deck is a long, fixed prefix reused across both passes; caching it
            # makes the second pass substantially cheaper.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=list(messages),
            output_format=output_format,
            thinking={"type": "adaptive"},
            output_config={"effort": self.settings.effort},
        )

    def extract(self, *, system: str, user: str, output_format: type[M]) -> M:
        """One structured extraction, with a single corrective retry.

        `messages.parse` enforces the JSON schema server-side, so a failure here is a
        constraint the schema cannot express -- and the fix is to show the model the
        error rather than to loosen the schema.
        """
        messages: list[Any] = [{"role": "user", "content": user}]

        try:
            response = self._call(system=system, messages=messages, output_format=output_format)
            self.usage.add(response.usage)
            parsed = response.parsed_output
            if parsed is None:
                raise ValueError("the response carried no parsed output")
            return parsed  # type: ignore[no-any-return]
        except (ValidationError, ValueError) as first_error:
            self.usage.retries += 1
            messages.append({"role": "assistant", "content": _echo(first_error)})
            messages.append(
                {"role": "user", "content": RETRY_USER.format(error=_render(first_error))}
            )
            try:
                response = self._call(system=system, messages=messages, output_format=output_format)
                self.usage.add(response.usage)
                parsed = response.parsed_output
                if parsed is None:
                    raise ValueError("the retry carried no parsed output")
                return parsed  # type: ignore[no-any-return]
            except (ValidationError, ValueError) as second_error:
                raise ExtractionValidationError(_render(second_error)) from second_error


def _render(error: Exception) -> str:
    """A validation error the model can act on, trimmed so it fits in the retry."""
    if isinstance(error, ValidationError):
        lines = [
            f"- {'.'.join(str(p) for p in item['loc'])}: {item['msg']}"
            for item in error.errors()[:20]
        ]
        extra = len(error.errors()) - 20
        if extra > 0:
            lines.append(f"- ... and {extra} more")
        return "\n".join(lines)
    return str(error)


def _echo(error: Exception) -> str:
    """Placeholder assistant turn so the retry reads as a correction, not a new task."""
    return f"I returned a response that failed validation: {type(error).__name__}."
