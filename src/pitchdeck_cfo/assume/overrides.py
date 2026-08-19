"""Read and write `assumptions.yaml` -- the user's seat at the top of the precedence chain.

The dump is deliberately not a bare mapping of name to number. Each entry carries
where the value came from and why, so someone editing the file can see at a glance
which lines are the company's own figures (leave alone) and which are benchmarks
standing in for missing data (the ones worth replacing).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from pitchdeck_cfo.assume.schema import ModelAssumptions, _walk
from pitchdeck_cfo.errors import PitchdeckCFOError
from pitchdeck_cfo.models import Sourced

HEADER = """\
# pitchdeck-cfo assumptions
#
# Every value below is tagged with where it came from:
#
#   deck       stated by the company, with the slide or page it appears on
#   derived    computed from other real figures; the note says how
#   benchmark  supplied by this tool because the deck did not state it
#   user       your own override
#
# The benchmark lines are the ones worth your attention. Replace any of them with
# your own figure and pass this file back:
#
#   pitchdeck-cfo build <deck> --assumptions assumptions.yaml
#
# Only the `value:` on each entry is read. Everything else is there so you can see
# what you are changing. Unknown keys are rejected rather than silently ignored.
"""


class AssumptionsFileError(PitchdeckCFOError):
    def __init__(self, path: Path, detail: str) -> None:
        super().__init__(
            f"{path.name} could not be used: {detail}",
            "Regenerate a clean file with `pitchdeck-cfo init-assumptions <deck>` and "
            "re-apply your edits to it.",
        )


def _entry(name: str, value: Sourced[Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {"value": value.value, "source": value.source}
    if value.citation:
        entry["citation"] = value.citation
    if value.note:
        entry["note"] = value.note
    entry["confidence"] = value.confidence
    return entry


def dump(assumptions: ModelAssumptions, resolved_names: dict[str, Sourced[Any]]) -> str:
    """Render the resolved assumptions as editable, annotated YAML."""
    coverage = assumptions.coverage
    body: dict[str, Any] = {
        "company": {
            "name": assumptions.company.name,
            "business_model": assumptions.company.business_model,
            "sector": assumptions.company.sector,
            "currency": assumptions.company.currency,
            "start_month": assumptions.company.start_month,
            "horizon_years": assumptions.company.horizon_years,
            "calendar_basis": assumptions.company.as_of_basis.value,
        },
        "coverage": {
            "core_inputs": coverage.total,
            "from_deck": coverage.from_deck,
            "from_benchmark": coverage.from_benchmark,
            "from_derived": coverage.from_derived,
            "from_user": coverage.from_user,
            "deck_ratio": round(coverage.deck_ratio, 3),
            "summary": coverage.summary_line(),
        },
        "assumptions": {
            name: _entry(name, value) for name, value in sorted(resolved_names.items())
        },
    }
    return HEADER + "\n" + yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=88)


def load(path: Path) -> dict[str, Any]:
    """Read an assumptions file into a flat name -> value mapping.

    Accepts both the annotated form this tool writes and a plain
    ``name: number`` mapping, since editing by hand tends to produce the latter.
    """
    if not path.exists():
        raise AssumptionsFileError(path, "no such file")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise AssumptionsFileError(path, f"invalid YAML -- {exc}") from exc

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AssumptionsFileError(path, "expected a mapping at the top level")

    section = raw.get("assumptions", raw)
    if not isinstance(section, dict):
        raise AssumptionsFileError(path, "the `assumptions:` section must be a mapping")

    flat: dict[str, Any] = {}
    for name, entry in section.items():
        if name in ("company", "coverage"):
            continue
        if isinstance(entry, dict):
            if "value" not in entry:
                raise AssumptionsFileError(path, f"`{name}` has no `value:` key")
            flat[name] = entry["value"]
        else:
            flat[name] = entry

    business_model = raw.get("company", {}).get("business_model") if "company" in raw else None
    if business_model:
        flat["business_model"] = business_model
    return flat


def resolved_names(assumptions: ModelAssumptions) -> dict[str, Sourced[Any]]:
    """Every `Sourced` leaf in the tree, keyed by its dotted path.

    Used for the dump. The engine keys by short name; this keys by location, which is
    what makes the file navigable.
    """
    return {path: value for path, value in _walk_named(assumptions)}


def _walk_named(node: object, path: str = "") -> list[tuple[str, Sourced[Any]]]:
    from pydantic import BaseModel

    found: list[tuple[str, Sourced[Any]]] = []
    if isinstance(node, Sourced):
        return [(path, node)]
    if isinstance(node, BaseModel):
        for name in type(node).model_fields:
            child = f"{path}.{name}" if path else name
            found.extend(_walk_named(getattr(node, name), child))
    elif isinstance(node, dict):
        for key, item in node.items():
            found.extend(_walk_named(item, f"{path}.{key}" if path else str(key)))
    elif isinstance(node, list | tuple):
        for i, item in enumerate(node):
            label = getattr(item, "name", None) or i
            found.extend(_walk_named(item, f"{path}[{label}]"))
    return found


__all__ = ["AssumptionsFileError", "dump", "load", "resolved_names"]

# Re-exported so callers do not reach into the schema module for it.
_ = _walk
