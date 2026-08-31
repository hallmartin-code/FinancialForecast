"""The deployment manifest must cover what the code actually imports.

This exists because of a real outage. `notify.py` imported httpx, which was
declared only in the dev extra and arrived in production merely as a transitive
dependency of the Anthropic SDK. When a fresh resolve on the host picked up
anthropic 1.x -- a major that swapped httpx for httpx2 -- the transitive path
disappeared, the app failed to import, and the platform quietly kept serving the
last deployment that booted. Local tests all passed throughout, because the dev
extra supplied httpx here.

So: every third-party module the shipped code imports is checked against
requirements.txt, which is the file the host actually builds from.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHIPPED = [ROOT / "app.py", *(ROOT / "src" / "pitchdeck_cfo").rglob("*.py")]

# Import name -> distribution, where the two differ and the mapping cannot be
# discovered from the installed environment.
KNOWN_ALIASES = {
    "yaml": "pyyaml",
    "dotenv": "python-dotenv",
    "pptx": "python-pptx",
    "fitz": "pymupdf",
    "PIL": "pillow",
    "dateutil": "python-dateutil",
    "multipart": "python-multipart",
}


def _normalise(name: str) -> str:
    return name.lower().replace("_", "-")


def _requirements() -> set[str]:
    """Distribution names in requirements.txt, the file the host builds from."""
    names: set[str] = set()
    for raw in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = line.split("[", 1)[0]
        for sep in (">=", "==", "<=", "~=", "!=", "<", ">"):
            name = name.split(sep, 1)[0]
        names.add(_normalise(name.strip()))
    return names


def _pyproject_runtime() -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names: set[str] = set()
    for spec in data["project"]["dependencies"]:
        name = spec.split("[", 1)[0]
        for sep in (">=", "==", "<=", "~=", "!=", "<", ">"):
            name = name.split(sep, 1)[0]
        names.add(_normalise(name.strip()))
    return names


def _module_level_imports(path: Path) -> set[str]:
    """Imports that run when the module is first imported, and so crash startup.

    An import inside a function is not one of these. That is the correct shape for
    an optional dependency -- `ingest/ocr.py` imports pytesseract and pdf2image
    that way on purpose, because the deployed image deliberately omits them and
    the code raises an actionable error instead. A module-level `try: import x
    except ImportError:` is the same deliberate guard, so it is exempt too.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()

    def guarded(node: ast.Try) -> bool:
        """A try/except that catches an import failure is a deliberate guard."""
        for handler in node.handlers:
            if handler.type is None:  # bare except swallows it too
                return True
            caught = ast.dump(handler.type)
            if "ImportError" in caught or "ModuleNotFoundError" in caught:
                return True
        return False

    def walk(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
            elif isinstance(node, ast.Try):
                if not guarded(node):
                    walk(node.body)
                walk(node.orelse)
                walk(node.finalbody)
            elif isinstance(node, ast.If):
                walk(node.body)
                walk(node.orelse)
            # Function and class bodies are deliberately not descended into.

    walk(tree.body)
    return found


def _third_party() -> dict[str, set[Path]]:
    """Every third-party module the shipped code imports, and where from."""
    stdlib = sys.stdlib_module_names
    mapping = packages_distributions()
    out: dict[str, set[Path]] = {}
    for path in SHIPPED:
        for module in _module_level_imports(path):
            if module in stdlib or module.startswith("_") or module == "pitchdeck_cfo":
                continue
            dists = mapping.get(module)
            dist = _normalise(dists[0]) if dists else KNOWN_ALIASES.get(module, _normalise(module))
            out.setdefault(dist, set()).add(path.relative_to(ROOT))
    return out


class TestDeploymentManifest:
    def test_every_shipped_import_is_declared_in_requirements(self) -> None:
        declared = _requirements()
        missing = {
            dist: sorted(str(p) for p in files)
            for dist, files in _third_party().items()
            if dist not in declared
        }
        assert not missing, (
            "these are imported by shipped code but absent from requirements.txt, "
            f"so the deployed image will not have them: {missing}"
        )

    def test_httpx_specifically_is_declared(self) -> None:
        # The regression that motivated this module. Named so a failure is unambiguous.
        assert "httpx" in _requirements()
        assert "httpx" in _pyproject_runtime()

    @pytest.mark.parametrize("pin", ["anthropic"])
    def test_the_sdk_major_is_capped(self, pin: str) -> None:
        """An uncapped major can change its dependency tree under a fresh resolve."""
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        line = next(ln for ln in text.splitlines() if ln.strip().startswith(pin))
        assert "<" in line, f"{pin} is uncapped: {line!r}"

    def test_requirements_and_pyproject_do_not_disagree(self) -> None:
        """requirements.txt installs `-e .`, so a conflict between them is a build risk."""
        shared = _requirements() & _pyproject_runtime()
        assert "anthropic" in shared and "httpx" in shared
