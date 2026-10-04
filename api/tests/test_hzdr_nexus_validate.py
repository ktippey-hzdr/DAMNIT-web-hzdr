"""The NeXus validation gate (campaign output phase 5) on the reference output.

The gate runs in nexus-design-studio's environment, which has pynxtools; this
suite runs it as a subprocess with that Python when it is found
(``HZDR_NDS_PYTHON``, else ``../nexus-design-studio/.venv/bin/python``) and
skips otherwise, like the shot-aligner sync checks without their sibling.
"""

from __future__ import annotations

import json
import os
import subprocess  # noqa: S404 -- a fixed interpreter and script
import sys
from pathlib import Path

import h5py
import pytest

from damnit_api.metadata import hzdr_containers as hc

from .test_hzdr_containers import FIXTURE, _build_master, _fixture_events, _read_path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "api" / "scripts" / "hzdr-nexus-validate.py"


def _nds_python() -> str | None:
    configured = os.environ.get("HZDR_NDS_PYTHON")
    candidates = [configured] if configured else []
    bin_dir = "Scripts" if sys.platform == "win32" else "bin"
    candidates.append(
        str(REPO.parent / "nexus-design-studio" / ".venv" / bin_dir / "python")
    )
    for python in candidates:
        if not python or not Path(python).is_file():
            continue
        probe = subprocess.run(  # noqa: S603
            [python, "-c", "import pynxtools, nexus_design_studio"],
            capture_output=True,
            check=False,
        )
        if probe.returncode == 0:
            return python
    return None


NDS_PYTHON = _nds_python()
pytestmark = pytest.mark.skipif(
    NDS_PYTHON is None,
    reason="no nexus-design-studio Python with pynxtools (set HZDR_NDS_PYTHON)",
)


def _gate(*args: str) -> subprocess.CompletedProcess:
    assert NDS_PYTHON is not None
    return subprocess.run(  # noqa: S603
        [NDS_PYTHON, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(scope="module")
def reference_output(tmp_path_factory) -> Path:
    """The reference shot as the live flow builds it: master, container, links."""
    out = tmp_path_factory.mktemp("validated")
    master = _build_master(out / "unassigned.nxs", _fixture_events())
    hc.convert_campaign(master, read_path=_read_path(FIXTURE / "raw"))
    _build_master(master, _fixture_events())  # now linking the container
    return master


def test_the_reference_output_passes_the_gate(reference_output):
    result = _gate("--master", str(reference_output))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Validation (unassigned.nxs): passed" in result.stdout
    report = json.loads(
        (reference_output.parent / ".validation.json").read_text(encoding="utf-8")
    )
    summary = report["summary"]
    assert summary["master_errors"] == 0
    assert summary["containers"] == 1
    assert summary["container_errors"] == 0
    entries = report["result"]["entries"]
    assert entries == [{"entry": "entry", "definition": "NXhzdr_target", "valid": True}]


def test_the_irr8_subentry_is_reported_with_what_it_lacks(reference_output):
    """Not certified yet: the concepts it lacks are mapping decisions."""
    result = _gate("--master", str(reference_output), "--no-write", "--json")
    report = json.loads(result.stdout[result.stdout.index("[") :])[0]
    container = report["containers"]["20251201_001042.nxs"]
    sub = container["subentries"]["Reflected_515_Spectrometer"]
    assert sub["definition"] == "NXoptical_spectroscopy"
    assert sub["valid"] is False
    assert any("experiment_type" in m for m in sub["findings"]["required_missing"])
    strict = _gate(
        "--master", str(reference_output), "--no-write", "--strict-subentries"
    )
    assert strict.returncode == 1
    assert "FAILED" in strict.stdout


def test_a_master_invalid_against_its_definition_fails_the_gate(
    reference_output, tmp_path
):
    bad = tmp_path / "bad.nxs"
    bad.write_bytes(reference_output.read_bytes())
    with h5py.File(bad, "r+") as handle:
        del handle["entry/start_time"]
        handle["entry/start_time"] = "not a date"
    result = _gate("--master", str(bad), "--no-write")
    assert result.returncode == 1
    assert "/entry is not valid against NXhzdr_target" in result.stdout


def test_a_structurally_broken_container_fails_the_gate(reference_output, tmp_path):
    folder = tmp_path / "campaign"
    (folder / "shots").mkdir(parents=True)
    master = folder / "campaign.nxs"
    master.write_bytes(reference_output.read_bytes())
    with h5py.File(folder / "shots" / "20251201_000001.nxs", "w") as handle:
        handle.create_group("not_an_entry")  # no NXentry, no classes
    result = _gate("--master", str(master), "--no-write")
    assert result.returncode == 1, result.stdout
    assert "20251201_000001.nxs:" in result.stdout
