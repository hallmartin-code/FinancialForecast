"""Command line surface.

Commands are thin: they parse flags, build a `Settings`, call into the pipeline and
render errors. No business logic lives here.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pitchdeck_cfo import __version__
from pitchdeck_cfo.config import MAX_YEARS, MIN_YEARS, Effort, load_settings
from pitchdeck_cfo.errors import DeckNotFoundError, PitchdeckCFOError, UnsupportedFileTypeError

app = typer.Typer(
    name="pitchdeck-cfo",
    help=(
        "Turn an investor deck into an auditable financial model and a one-page "
        "summary. Every number is traceable to the deck, an arithmetic derivation, "
        "or a labelled benchmark."
    ),
    no_args_is_help=True,
    add_completion=False,
)

console = Console()
err_console = Console(stderr=True)

SUPPORTED_SUFFIXES = {".pdf", ".pptx"}


# --------------------------------------------------------------------------- #
# shared option types
# --------------------------------------------------------------------------- #

DeckArg = Annotated[
    Path,
    typer.Argument(
        help="Path to the investor deck (.pdf or .pptx).",
        show_default=False,
    ),
]
ModelOpt = Annotated[
    str | None,
    typer.Option("--model", help="Claude model id. Defaults to PDCFO_MODEL or claude-opus-5."),
]
EffortOpt = Annotated[
    Effort | None,
    typer.Option("--effort", help="Reasoning effort for the extraction passes."),
]
OCROpt = Annotated[
    bool,
    typer.Option("--ocr", help="Rasterise and OCR a scanned deck. Needs tesseract + poppler."),
]
NoCacheOpt = Annotated[
    bool,
    typer.Option("--no-cache", help="Ignore any cached extraction for this deck."),
]
VerboseOpt = Annotated[
    bool,
    typer.Option("--verbose", "-v", help="Show stage detail, token accounting and raw responses."),
]


def _validate_deck_path(path: Path) -> Path:
    """Fail on a bad path before spending a single API call on it."""
    if not path.exists():
        raise DeckNotFoundError(path)
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise UnsupportedFileTypeError(path)
    return path


def _fail(exc: PitchdeckCFOError) -> None:
    """Render an actionable error and exit non-zero."""
    body = f"[bold]{exc.message}[/bold]"
    if exc.remedy:
        body += f"\n\n[dim]{exc.remedy}[/dim]"
    err_console.print(Panel(body, title="[red]pitchdeck-cfo[/red]", border_style="red"))
    raise typer.Exit(code=1)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"pitchdeck-cfo {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    """pitchdeck-cfo"""


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


@app.command()
def build(
    deck: DeckArg,
    out: Annotated[Path, typer.Option("--out", help="Directory for the two artifacts.")] = Path(
        "./output"
    ),
    years: Annotated[
        int | None,
        typer.Option("--years", help=f"Forecast horizon, {MIN_YEARS}-{MAX_YEARS}."),
    ] = None,
    assumptions: Annotated[
        Path | None,
        typer.Option("--assumptions", help="assumptions.yaml whose values override everything."),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Refuse to emit a model whose core inputs are mostly benchmarks.",
        ),
    ] = False,
    model: ModelOpt = None,
    effort: EffortOpt = None,
    ocr: OCROpt = False,
    no_cache: NoCacheOpt = False,
    verbose: VerboseOpt = False,
) -> None:
    """Build the one-pager PDF and the financial model workbook."""
    try:
        _validate_deck_path(deck)
        load_settings(model=model, effort=effort, years=years)
    except PitchdeckCFOError as exc:
        _fail(exc)
    raise NotImplementedError("build pipeline lands in phase 7; phases 3-6 build its stages")


@app.command()
def extract(
    deck: DeckArg,
    json_out: Annotated[
        Path | None,
        typer.Option("--json", help="Write the extracted facts here instead of stdout."),
    ] = None,
    model: ModelOpt = None,
    effort: EffortOpt = None,
    ocr: OCROpt = False,
    no_cache: NoCacheOpt = False,
    verbose: VerboseOpt = False,
) -> None:
    """Run the extraction passes only and show what the deck actually states."""
    from pitchdeck_cfo.extract import default_cache, extract_facts
    from pitchdeck_cfo.ingest import load_deck

    try:
        _validate_deck_path(deck)
        settings = load_settings(model=model, effort=effort)

        with console.status("[dim]reading deck[/dim]", spinner="dots") as status:
            document = load_deck(deck, ocr=ocr, min_text_chars=settings.min_text_chars)
            status.update(f"[dim]{document.summary()}[/dim]")

            def progress(message: str) -> None:
                status.update(f"[dim]{message}[/dim]")

            facts = extract_facts(
                document,
                settings=settings,
                cache=default_cache(settings, enabled=not no_cache),
                progress=progress,
            )
    except PitchdeckCFOError as exc:
        _fail(exc)

    payload = facts.model_dump_json(indent=2)
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(payload, encoding="utf-8")
        console.print(f"[green]wrote[/green] {json_out}")
    else:
        console.print_json(payload)

    if verbose or facts.grounding_warnings:
        _report_grounding(facts)


def _report_grounding(facts: object) -> None:
    """Show any quote that could not be found where the model said it was."""
    warnings = getattr(facts, "grounding_warnings", ())
    if not warnings:
        console.print("[green]grounding: every quote was found on the page it cited.[/green]")
        return

    table = Table(
        title="Quotes that could not be confirmed",
        title_style="yellow",
        header_style="dim",
    )
    table.add_column("field")
    table.add_column("cited")
    table.add_column("problem")
    table.add_column("quote", overflow="fold", max_width=48)
    for warning in warnings:
        table.add_row(
            warning.field_path,
            warning.citation,
            warning.reason.replace("_", " "),
            warning.quote,
        )
    err_console.print(table)


@app.command(name="init-assumptions")
def init_assumptions(
    deck: DeckArg,
    years: Annotated[int | None, typer.Option("--years")] = None,
    model: ModelOpt = None,
    ocr: OCROpt = False,
    no_cache: NoCacheOpt = False,
    verbose: VerboseOpt = False,
) -> None:
    """Print the resolved assumptions as editable YAML, with provenance on every value."""
    try:
        _validate_deck_path(deck)
        load_settings(model=model, years=years)
    except PitchdeckCFOError as exc:
        _fail(exc)
    raise NotImplementedError("assumption resolution lands in phase 3")


@app.command()
def validate(
    assumptions: Annotated[
        Path,
        typer.Argument(help="An assumptions.yaml to check against the schema.", show_default=False),
    ],
    verbose: VerboseOpt = False,
) -> None:
    """Check an assumptions file for schema and internal-consistency errors, offline."""
    if not assumptions.exists():
        _fail(
            PitchdeckCFOError(
                f"No such assumptions file: {assumptions}",
                "Generate one with `pitchdeck-cfo init-assumptions <deck> > assumptions.yaml`.",
            )
        )
    raise NotImplementedError("assumption validation lands in phase 3")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
