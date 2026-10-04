"""The vendored reference fixture: phase 1 of the campaign output plan.

`tests/fixtures/hzdr-reference/` is shot-aligner's reference shot (see its
README and SOURCE.json): three instruments, the hzdr-event-v1 events
planet-watchdog would send for them, and a manifest of the container
shot-aligner builds. The container writer this repository gains in phase 3 is
held to that manifest. Until then this module pins what it will rely on.

Re-vendor with `hzdr/scripts/sync-hzdr-reference.sh --apply` (or the `.ps1`
with `-Apply`); never edit the copy by hand.
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

# The parts of a container the writer must reproduce are the instrument data.
# Everything below is left out, each for its reason (the fixture README says
# the same).
NOT_CONTRACT = (
    # shot-aligner's own alignment evidence; the live flow keeps attribution
    # in /entry/source_events.
    "/entry/alignment",
    # Who built it, with what.
    "/entry/build_provenance",
    "/entry/program_name",
    "/entry/user",
    # The workbook row; DAMNIT takes it from LabFrog.
    "/entry/shot_info",
    "/entry/shot_parameters",
    "/entry/shotsheet_provenance",
    # shot-aligner's naming of the shot (the title reads the date's project
    # from shot-aligner's own indexes).
    "/entry/title",
    "/entry/start_time",
    "/entry/entry_identifier",
    "/entry/collection_identifier",
    # December's beamtime description and link; the generator now leaves them
    # out, and they are not the writer's business either way.
    "/entry/experiment_description",
    "/entry/experiment_documentation",
    # The entry-level plot: decision 6 keeps only each detector's default.
    "/entry/data",
    # NXsubentry groups carrying the mappings' application-definition claims
    # (NXxrd_pan for a camera among them), until phase 2 reconciles the
    # mappings (decision 5).
    "/entry/Reflected_515_Spectrometer",
    "/entry/_515_Reflected_Light_Spectrometer",
)
# Attributes left out for the same reasons: the entry's `default` names the
# entry-level plot, `nds_definition` is a mapping's claim (until phase 2).
NOT_CONTRACT_ATTRIBUTES = {"/entry": ("default",), "*": ("nds_definition",)}


def _events() -> list[dict]:
    lines = (FIXTURE / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _manifest() -> dict:
    return json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))


def _in_contract(path: str) -> bool:
    return not any(path == p or path.startswith(p + "/") for p in NOT_CONTRACT)


def contract_nodes(manifest: dict) -> dict:
    """The manifest nodes a DAMNIT-built container must match.

    The manifest lists every link name; an alias carries `link` to the
    lexicographically first name of its object. When that name is outside the
    contract (a subentry copy), the first name inside it becomes canonical,
    so every `link` here points at a contract node.
    """
    nodes = {}
    for path, node in manifest["nodes"].items():
        if not _in_contract(path):
            continue
        dropped = NOT_CONTRACT_ATTRIBUTES.get(path, ()) + NOT_CONTRACT_ATTRIBUTES["*"]
        nodes[path] = {k: v for k, v in node.items() if k not in dropped}
    groups: dict[str, list[str]] = {}
    for path, node in nodes.items():
        if node["type"] in ("group", "dataset"):
            groups.setdefault(node.get("link", path), []).append(path)
    for paths in groups.values():
        canonical = min(paths)
        for path in paths:
            nodes[path].pop("link", None)
            if path != canonical:
                nodes[path]["link"] = canonical
    return nodes


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


def test_a_product_row_lacks_its_instrument_and_the_join_recovers_it_phase3_gap():
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
    # Only the camera's rule groups by stem (PNG + CSV sidecar); each frame of
    # the recording is an event of its own.
    multi = [e for e in events if len(e["payload_ref"].get("members", [])) > 1]
    assert {e["metadata"]["instrument"]["id"] for e in multi} == {"m1_spec_fib_cer"}
    frames = [e for e in events if e["metadata"]["instrument"]["id"] == "probe135"]
    assert len(frames) == 2


# The file a pack reads first: the event's primary file, wherever sorting
# puts it among the members (the camera's CSV sorts before its PNG).
MEASUREMENT_SUFFIX = {
    "camera_png_csv": "_original.png",
    "spectrometer_irr8": ".Irr8.txt",
    "sequence_frames": ".tif",
}


def test_the_primary_file_is_the_measurement_file():
    for event in _events():
        ref = event["payload_ref"]
        suffix = MEASUREMENT_SUFFIX[event["metadata"]["instrument"]["format"]]
        assert ref["path"].endswith(suffix), ref["path"]
        assert ref["path"] in [m["path"] for m in ref.get("members") or [ref]]


def test_packs_are_routed_by_instrument_format_never_by_kind():
    """`kind` is `watchdog.<watch_name>`, the rule's name, not the pack.

    A consumer that chose the pack from `kind` would find none of them; the
    pack is `metadata.instrument.format`, and every instrument of the
    manifest is reached through it.
    """
    packs = {i["pack"] for i in _manifest()["instruments"]}
    routed = set()
    for event in _events():
        fmt = event["metadata"]["instrument"]["format"]
        assert fmt in packs
        assert event["kind"].startswith("watchdog.")
        assert event["kind"].removeprefix("watchdog.") not in packs
        assert not any(pack in event["kind"] for pack in packs)
        routed.add((event["metadata"]["instrument"]["id"], fmt))
    assert routed == {(i["id"], i["pack"]) for i in _manifest()["instruments"]}


@pytest.mark.parametrize(
    ("suffix", "shape"),
    [
        ("/data/image", [2, 4, 5]),  # the two-frame recording, one stack
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
    assert {"NXinstrument", "NXdetector"} <= classes


def test_contract_excludes_what_the_plan_drops():
    """Definition claims (until phase 2), the entry plot, the beamtime prose."""
    nodes = contract_nodes(_manifest())
    assert "NXsubentry" not in {n["NX_class"] for n in nodes.values()}
    assert not any("nds_definition" in n for n in nodes.values())
    assert "default" not in nodes["/entry"]
    for dropped in ("/entry/data", "/entry/title", "/entry/experiment_documentation"):
        assert dropped not in nodes
    # Each detector keeps its own default plot.
    detectors = [n for n in nodes.values() if n["NX_class"] == "NXdetector"]
    assert detectors
    assert all(n.get("default") == "data" for n in detectors)


def test_every_link_name_is_in_the_contract_and_aliases_point_inside_it():
    nodes = contract_nodes(_manifest())
    # Hard-linked aliases are listed, not skipped as `visititems` would.
    assert nodes["/entry/Probe_135_deg/pco_Camera/raw_data/image"]["link"] == (
        "/entry/Probe_135_deg/pco_Camera/data/image"
    )
    for path, node in nodes.items():
        if "link" in node:
            assert node["link"] in nodes, path
            assert "link" not in nodes[node["link"]], path
