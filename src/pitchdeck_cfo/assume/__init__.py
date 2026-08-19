"""Assume stage: `DeckFacts` + benchmarks + user overrides -> `ModelAssumptions`.

The only stage allowed to fill a gap the deck left, and every gap it fills is
labelled with where the number came from.
"""

from __future__ import annotations

from pitchdeck_cfo.assume.benchmarks import BenchmarkValue, pack_for, published_count
from pitchdeck_cfo.assume.engine import CORE_INPUTS, Resolver, resolve
from pitchdeck_cfo.assume.overrides import AssumptionsFileError, dump, load, resolved_names
from pitchdeck_cfo.assume.schema import ModelAssumptions

__all__ = [
    "CORE_INPUTS",
    "AssumptionsFileError",
    "BenchmarkValue",
    "ModelAssumptions",
    "Resolver",
    "dump",
    "load",
    "pack_for",
    "published_count",
    "resolve",
    "resolved_names",
]
