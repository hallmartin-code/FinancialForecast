"""On-disk cache for extraction results.

Rendering is the part of this tool that gets iterated on, and re-running extraction
for every layout tweak would be slow and expensive for no benefit. Results are keyed
by the deck's content hash plus everything that could change the answer, so a stale
entry cannot be served after a prompt or model change.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

M = TypeVar("M", bound=BaseModel)

_CHUNK = 1 << 20


def file_sha256(path: Path) -> str:
    """Content hash of the deck. Renaming a deck must not invalidate its cache."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def cache_key(*, deck_sha256: str, prompt_version: str, model: str, pass_name: str) -> str:
    """Everything that could change the answer goes into the key.

    Model and prompt version are included deliberately: a cache hit across either of
    those would silently mix outputs from two different extractors in one run.
    """
    material = f"{deck_sha256}|{prompt_version}|{model}|{pass_name}"
    return hashlib.sha256(material.encode()).hexdigest()[:32]


class ExtractionCache:
    """A directory of JSON documents, one per (deck, prompt, model, pass)."""

    def __init__(self, directory: Path, *, enabled: bool = True) -> None:
        self.directory = directory
        self.enabled = enabled

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def load(self, key: str, model_type: type[M]) -> M | None:
        """Return the cached value, or None when absent, unreadable or stale.

        A corrupt or schema-incompatible entry is treated as a miss rather than an
        error: the cache is an optimisation, and failing a run because of one is
        worse than paying for the call again.
        """
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            return model_type.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValidationError, json.JSONDecodeError, OSError):
            return None

    def store(self, key: str, value: BaseModel) -> None:
        """Write atomically so an interrupted run cannot leave a half-written entry."""
        if not self.enabled:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self._path(key)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(value.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(target)
