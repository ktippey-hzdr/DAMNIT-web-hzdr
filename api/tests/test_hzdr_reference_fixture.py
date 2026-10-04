"""The vendored reference fixture: phase 1 of the campaign output plan.

`tests/fixtures/hzdr-reference/` is shot-aligner's reference shot (see its
README and SOURCE.json): three instruments, the hzdr-event-v1 events
planet-watchdog would send for them, and a manifest of the container
shot-aligner builds. The container writer this repository gains in phase 3 is
held to that manifest. Until then this module pins what it will rely on.
"""

import hashlib
import json
from pathlib import Path

import pytest

from damnit_api.metadata.hzdr_event import HZDREventV1
from damnit_api.metadata.hzdr_nexus import _normalize_event, build_event_data_products
from damnit_api.shared.hzdr_paths import map_path, parse_path_map

FIXTURE = Path(__file__).parent / "fixtures" / "hzdr-reference"
RECORDED_ROOT = "/bigdata/HPLexp/reference-fixture"

# The parts of a container the writer must reproduce: instrument data, not
# shot-aligner's own alignment evidence, build provenance or workbook row.
NOT_CONTRACT = (
    "/entry/alignment",
    "/entry/build_provenance",
    "/entry/program_name",
    "/entry/user",
    "/entry/shot_info",
    "/entry/shot_parameters",
    "/entry/shotsheet_provenance",
)


def _events() -> list[dict]:
    lines = (FIXTURE / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _manifest() -> dict:
    return json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))


def contract_nodes(manifest: dict) -> dict:
    """The manifest nodes a DAMNIT-built container must match."""
    return {
        path: node
        for path, node in manifest["nodes"].items()
        if not any(path == p or path.startswith(p + "/") for p in NOT_CONTRACT)
    }


def test_vendored_files_are_the_recorded_copy():
    """A hand edit shows up here; re-vendor from shot-aligner instead."""
    source = json.loads((FIXTURE / "SOURCE.json").read_text(encoding="utf-8"))
    on_disk = {
        str(p.relative_to(FIXTURE)).replace("\\", "/")
        for p in FIXTURE.rglob("*")
        if p.is_file() and p.name != "SOURCE.json"
    }
    assert on_disk == set(source["files"])
    for name, digest in source["files"].items():
        assert hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest() == digest, name


def test_events_are_valid_hzdr_event_v1():
    for event in _events():
        HZDREventV1.model_validate(event)


def test_recorded_paths_reach_the_raws_through_the_path_map():
    rules = parse_path_map(f"{RECORDED_ROOT}={FIXTURE / 'raw'}")
    for event in _events():
        ref = event["payload_ref"]
        for member in ref.get("members") or [ref]:
            local = map_path(member["path"], rules)
            assert local is not None, member["path"]
            assert local.is_file(), member["path"]
            assert hashlib.sha256(local.read_bytes()).hexdigest() == member["sha256"]


def test_a_product_row_reaches_its_instrument_and_members_by_event_id():
    """What a pack needs is not on the product row yet; the join recovers it."""
    events = [_normalize_event(event) for event in _events()]
    by_id = {event["event_id"]: event for event in events}
    products = build_event_data_products(
        events, shot_key="reference-fixture:20251201:001042"
    )

    assert len(products) == len(events)
    for product in products:
        assert "instrument" not in product["metadata"]  # the phase-3 gap
        event = by_id[product["metadata"]["event_id"]]
        instrument = event["metadata"]["instrument"]
        assert instrument["format"] in {
            "camera_png_csv",
            "spectrometer_irr8",
            "sequence_frames",
        }
        assert product["path"] == event["payload_ref"]["path"]
    multi = [e for e in events if len(e["payload_ref"].get("members", [])) > 1]
    assert {e["metadata"]["instrument"]["id"] for e in multi} == {
        "m1_spec_fib_cer",
        "probe135",
    }


@pytest.mark.parametrize(
    ("suffix", "shape"),
    [
        ("/data/image", [3, 4, 5]),  # the recording, one stack
        ("/data/absolute_irradiance", None),  # the spectrum
    ],
)
def test_manifest_holds_the_instrument_data(suffix, shape):
    nodes = contract_nodes(_manifest())
    hits = [n for p, n in nodes.items() if p.endswith(suffix)]
    assert hits, suffix
    if shape is not None:
        assert shape in [n["shape"] for n in hits]


def test_contract_excludes_shot_aligner_bookkeeping():
    nodes = contract_nodes(_manifest())
    assert not any(p.startswith("/entry/alignment") for p in nodes)
    classes = {n["NX_class"] for n in nodes.values()}
    assert {"NXinstrument", "NXdetector", "NXsubentry"} <= classes
