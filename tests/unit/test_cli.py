"""CLI contract: help works, bad input fails with a remedy, unbuilt stages do not lie."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from pitchdeck_cfo import __version__
from pitchdeck_cfo.cli import app

runner = CliRunner()


class TestHelp:
    def test_bare_invocation_shows_help(self) -> None:
        result = runner.invoke(app, [])
        # click's convention for a bare group invocation is usage + exit 2, not 0.
        assert result.exit_code == 2
        for cmd in ("build", "extract", "init-assumptions", "validate"):
            assert cmd in result.output

    def test_version(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    @pytest.mark.parametrize("cmd", ["build", "extract", "init-assumptions", "validate"])
    def test_every_command_has_help(self, cmd: str) -> None:
        assert runner.invoke(app, [cmd, "--help"]).exit_code == 0


class TestInputValidation:
    def test_missing_deck_is_actionable(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["build", str(tmp_path / "nope.pdf")])
        assert result.exit_code == 1
        assert "No such deck" in result.output

    def test_wrong_file_type_is_actionable(self, tmp_path: Path) -> None:
        bad = tmp_path / "notes.txt"
        bad.write_text("hello")
        result = runner.invoke(app, ["build", str(bad)])
        assert result.exit_code == 1
        assert "Export the deck to PDF or PowerPoint" in result.output

    def test_bad_path_fails_before_any_api_call(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        # No credential in the environment at all: the path check must still be what fails,
        # proving we validate cheap things before reaching for the network.
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = runner.invoke(app, ["extract", str(tmp_path / "nope.pptx")])
        assert "No such deck" in result.output


class TestNoSilentStubs:
    """An unbuilt stage must raise, never return plausible-looking data."""

    def test_build_raises_rather_than_faking_output(self, tmp_path: Path) -> None:
        deck = tmp_path / "d.pdf"
        deck.write_bytes(b"%PDF-1.4\n")
        result = runner.invoke(app, ["build", str(deck)])
        assert isinstance(result.exception, NotImplementedError)

    def test_validate_missing_file_is_actionable_not_notimplemented(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["validate", str(tmp_path / "a.yaml")])
        assert result.exit_code == 1
        assert "init-assumptions" in result.output
