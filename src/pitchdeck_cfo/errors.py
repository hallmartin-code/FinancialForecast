"""Typed failures with actionable remedies.

Every error a user can hit carries the sentence that tells them what to do next.
`cli.py` renders `.remedy` in a distinct style; nothing else is allowed to print
a bare traceback at the user.
"""

from __future__ import annotations

from pathlib import Path


class PitchdeckCFOError(Exception):
    """Base class. `remedy` is shown to the user beneath the message."""

    remedy: str = ""

    def __init__(self, message: str, remedy: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if remedy is not None:
            self.remedy = remedy


class MissingCredentialError(PitchdeckCFOError):
    def __init__(self) -> None:
        super().__init__(
            "No Anthropic credential found.",
            "Set ANTHROPIC_API_KEY in your environment or in a .env file next to the "
            "deck (see .env.example), or run `ant auth login`.",
        )


class UnsupportedFileTypeError(PitchdeckCFOError):
    def __init__(self, path: Path) -> None:
        super().__init__(
            f"Cannot read {path.name}: expected a .pdf or .pptx investor deck, "
            f"got '{path.suffix or 'no extension'}'.",
            "Export the deck to PDF or PowerPoint and try again.",
        )


class DeckNotFoundError(PitchdeckCFOError):
    def __init__(self, path: Path) -> None:
        super().__init__(
            f"No such deck: {path}",
            "Check the path. Quote it if it contains spaces.",
        )


class CorruptDeckError(PitchdeckCFOError):
    """The file has the right extension but cannot be parsed.

    Matters most for the web app, where the file arrives from a browser and may be
    truncated by an interrupted upload or simply mislabelled.
    """

    def __init__(self, path: Path, detail: str) -> None:
        super().__init__(
            f"{path.name} could not be read: {detail}",
            "The file looks damaged or is not really the format its extension claims. "
            "Re-export the deck and try again.",
        )


class InsufficientTextError(PitchdeckCFOError):
    """A PDF that yields almost no text is a scan, not a text PDF."""

    def __init__(self, path: Path, chars: int, threshold: int) -> None:
        super().__init__(
            f"{path.name} yielded only {chars} characters of text "
            f"(a readable deck yields at least {threshold}). It is almost certainly "
            f"a scanned or image-only PDF.",
            "Re-run with --ocr. That needs the tesseract and poppler binaries on PATH.",
        )


class OCRUnavailableError(PitchdeckCFOError):
    def __init__(self, missing: str) -> None:
        super().__init__(
            f"--ocr was requested but {missing} is not available.",
            "On Windows: install Tesseract (github.com/UB-Mannheim/tesseract) and "
            "poppler (github.com/oschwartz10612/poppler-windows), add both bin/ "
            "directories to PATH, then `pip install 'pitchdeck-cfo[ocr]'`.",
        )


class ExtractionValidationError(PitchdeckCFOError):
    """The model's JSON failed schema validation twice -- once on the retry too."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"The extraction response did not match the schema after a retry: {detail}",
            "Re-run with --verbose to see the raw response. If it persists, the deck "
            "may be unusually structured -- open an issue with the deck attached.",
        )


class InsufficientCoverageError(PitchdeckCFOError):
    """--strict refusing to emit a confident-looking model built mostly on benchmarks."""

    def __init__(self, sourced: int, total: int, threshold: float) -> None:
        pct = (sourced / total * 100) if total else 0.0
        super().__init__(
            f"--strict: only {sourced} of {total} core inputs ({pct:.0f}%) came from the "
            f"deck, below the {threshold:.0%} threshold. The model would be mostly "
            f"benchmark, and presenting it as this company's forecast would mislead.",
            "Either re-run without --strict to get the model with its provenance marks "
            "intact, or supply the missing inputs via --assumptions.",
        )


class UnsupportedBusinessModelError(PitchdeckCFOError):
    """Better a clear refusal than a marketplace modelled as if it were SaaS."""

    def __init__(self, business_model: str, supported: tuple[str, ...]) -> None:
        super().__init__(
            f"No revenue engine is implemented for business model '{business_model}'.",
            f"Supported today: {', '.join(supported)}. Override the classification with "
            f"`business_model:` in an --assumptions file if this deck was misread.",
        )
