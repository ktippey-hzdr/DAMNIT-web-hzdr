"""Guards that keep the published coverage map describing the real suite.

The map's areas are hand-maintained. Without these checks a new module simply
never appears in it, so the headline percentage quietly describes a smaller
codebase than the one that ships -- and a module nobody assigned to an area is
exactly the kind that goes untested.

Only `damnit_api` is in scope. The frontend has its own suite and is not
measured by this map.

Loaded by path rather than imported as ``scripts.docs...``: `scripts/` is not a
package and must not become one.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "api" / "src" / "damnit_api"
TESTS_DIR = REPO_ROOT / "api" / "tests"


def _load_coverage_map():
    path = REPO_ROOT / "scripts" / "docs" / "refresh_coverage_map.py"
    name = "refresh_coverage_map"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None, f"cannot load {path}"
    assert spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    previously = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previously
    return module


coverage_map = _load_coverage_map()


def _mapped_source_files() -> set[str]:
    return {path for area in coverage_map.AREAS for path in area.files}


def _mapped_test_modules() -> set[str]:
    return {path for area in coverage_map.AREAS for path in area.tests}


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT)).replace("\\", "/")


def test_every_mapped_source_file_exists() -> None:
    missing = sorted(
        path for path in _mapped_source_files() if not (REPO_ROOT / path).is_file()
    )
    assert not missing, f"coverage map points at source files that are gone: {missing}"


def test_every_mapped_test_file_exists() -> None:
    missing = sorted(
        path for path in _mapped_test_modules() if not (REPO_ROOT / path).is_file()
    )
    assert not missing, f"coverage map points at tests that are gone: {missing}"


def test_every_source_file_is_mapped_or_explicitly_exempt() -> None:
    on_disk = {
        _rel(path)
        for path in PACKAGE_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    }
    unaccounted = sorted(
        on_disk - _mapped_source_files() - coverage_map.UNMAPPED_SOURCE_FILES
    )
    assert not unaccounted, (
        "these modules are in neither a coverage area nor "
        f"UNMAPPED_SOURCE_FILES: {unaccounted}"
    )


def test_every_test_module_is_mapped_or_explicitly_exempt() -> None:
    on_disk = {_rel(path) for path in TESTS_DIR.glob("test_*.py")}
    unaccounted = sorted(
        on_disk - _mapped_test_modules() - coverage_map.UNMAPPED_TEST_MODULES
    )
    assert not unaccounted, (
        "these test modules are in neither a coverage area nor "
        f"UNMAPPED_TEST_MODULES: {unaccounted}"
    )


def test_exemption_lists_have_no_stale_entries() -> None:
    """An exemption for a deleted file hides the next real gap."""
    stale = sorted(
        path
        for path in (
            coverage_map.UNMAPPED_SOURCE_FILES | coverage_map.UNMAPPED_TEST_MODULES
        )
        if not (REPO_ROOT / path).is_file()
    )
    assert not stale, f"exemption lists name files that do not exist: {stale}"


def test_contributing_carries_the_markers() -> None:
    """Without both markers the refresh script has nowhere to write."""
    text = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert coverage_map.START_MARKER in text
    assert coverage_map.END_MARKER in text
