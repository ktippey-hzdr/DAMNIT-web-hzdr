# h5py's `Group.__getitem__` is typed as Group | Dataset | Datatype, so every
# `handle["entry/..."]` in an assertion needs narrowing pyright cannot infer.
# pyright: reportIndexIssue=false, reportAttributeAccessIssue=false
# pyright: reportArgumentType=false, reportOperatorIssue=false, reportCallIssue=false
"""The container writer (campaign output plan phase 3).

The design is `hzdr/docs/plans/container-writer.md`. The exit check is the
first section: the reference fixture's events, built into a master by the real
builder functions and converted with the raws reached through the path map,
give a container that matches the manifest's contract (minus the mapping-row
nodes phase 3 leaves out) and whose detectors equal the per-pack references.
The rest pins the worker: incremental, resumable, missing files, names, the
lock it holds, memory.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess  # noqa: S404 -- the interpreter itself, fixed arguments
import sys
import time
import weakref
from pathlib import Path

import h5py
import numpy as np
import pytest

from damnit_api.metadata import hzdr_containers as hc
from damnit_api.metadata.hzdr_nexus import (
    _normalize_event,
    reconcile_canonical_shots,
    single_writer_lock,
    write_nexus_bridge,
)
from damnit_api.metadata.hzdr_packs import sequence_frames

from .test_hzdr_packs import walk
from .test_hzdr_reference_fixture import NOT_CONTRACT, contract_nodes

FIXTURE = Path(__file__).parent / "fixtures" / "hzdr-reference"
RECORDED_ROOT = "/bigdata/HPLexp/reference-fixture"
WORKER = Path(__file__).resolve().parents[1] / "scripts" / "hzdr-container-worker.py"
CONTAINER = "20251201_001042.nxs"

# The manifest nodes shot-aligner's reviewed mapping rows create
# (`mappings.apply_to`), which phase 3 leaves out (design note, section 5).
CAMERA = "/entry/Reflected_light_spectroscopy/_515_Reflected_Light_Spectrometer"
MAPPING_ROW_NODES = {
    "/entry/collection_M1_Spec_Fib_Cer",
    "/entry/collection_M1_Spec_Fib_Cer/black_level",
    "/entry/collection_M1_Spec_Fib_Cer/chip_size_x",
    "/entry/collection_M1_Spec_Fib_Cer/chip_size_y",
    "/entry/collection_M1_Spec_Fib_Cer/gamma",
    "/entry/collection_M1_Spec_Fib_Cer/image_file",
    f"{CAMERA}/description",
    f"{CAMERA}/frame_start_number",
}
# ... and the aliases they make of nodes the pack still writes.
MAPPING_ROW_ALIASES = {
    f"{CAMERA}/fabrication/model",
    f"{CAMERA}/raw_data/sequence_number",
}
# Shot-level fields the writer adds that the manifest leaves out of the contract
# (DAMNIT's own naming of the shot and its problems note).
CONTAINER_OWN = (
    "/entry/experiment_identifier",
    "/entry/conversion_problems",
    "/entry/labfrog_shot",
)
DETECTORS = {
    "camera_png_csv": CAMERA,
    "spectrometer_irr8": (
        "/entry/Reflected_light_spectroscopy/Reflected_515_Spectrometer"
    ),
    "sequence_frames": "/entry/Probe_135_deg/pco_Camera",
}
# What `compose_shot` adds to each detector around the pack's own output.
AROUND_THE_PACK = ("local_name", "timing_role", "file_metadata")
MANIFEST_KEYS = ("type", "NX_class", "shape", "dtype", "units", "signal", "axes")


def _fixture_events() -> list[dict]:
    lines = (FIXTURE / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _manifest() -> dict:
    return json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))


def _read_path(raw_root: Path):
    return hc.make_read_path(f"{RECORDED_ROOT}={raw_root.as_posix()}")


def _build_master(path: Path, events: list[dict]) -> Path:
    """Publish a master the way the builder does, from raw hzdr-event-v1 events."""
    normalized = [_normalize_event(copy.deepcopy(event)) for event in events]
    shots, assigned = reconcile_canonical_shots(
        normalized,
        experiment_id="unassigned",
        source_key="unassigned",
        labfrog_shots=[],
        campaign_timezone="Europe/Berlin",
    )
    with single_writer_lock(path):
        write_nexus_bridge(
            output_path=path,
            experiment_id="unassigned",
            shots=shots,
            events=assigned,
            seed_from_output=False,
        )
    return path


def _shot_events(number: int, raw_root: Path) -> list[dict]:
    """The fixture's events moved to shot `number`, with raws of their own.

    Each shot's raws are a copy of the fixture's under `<raw_root>/<number>/`,
    recorded as `/bigdata/.../<number>/...`, so changing one shot's bytes
    touches no other shot.
    """
    events = []
    shift = number - 1042
    for original in _fixture_events():
        event = copy.deepcopy(original)
        event["event_id"] = f"{original['event_id']}-{number}"
        event["shot_number"] = number
        event["shot_id"] = f"shot-{number:06d}"
        ref = event["payload_ref"]
        for item in [ref, *(ref.get("members") or [])]:
            relative = item["path"].removeprefix(RECORDED_ROOT + "/")
            source = FIXTURE / "raw" / relative
            target = raw_root / str(number) / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(source.read_bytes())
            item["path"] = f"{RECORDED_ROOT}/{number}/{relative}"
        for trigger in event["metadata"].get("zmq_data") or []:
            trigger["payload"]["shot_number"] = number
        event["timestamp"] = event["timestamp"].replace(
            "14:59:0", f"14:{59 - shift:02d}:0"
        )
        events.append(event)
    return events


@pytest.fixture
def campaign(tmp_path):
    """Four shots (1042..1045) in a master, raws under tmp_path/raw."""
    raw = tmp_path / "raw"
    events = [e for n in range(1042, 1046) for e in _shot_events(n, raw)]
    master = _build_master(tmp_path / "out" / "unassigned.nxs", events)
    return {"master": master, "raw": raw, "events": events, "tmp": tmp_path}


def _mtimes(directory: Path) -> dict[str, int]:
    return {p.name: p.stat().st_mtime_ns for p in sorted(directory.glob("*.nxs"))}


# ---------------------------------------------------------------------------
# Exit check: the reference fixture's container.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reference_container(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("reference")
    master = _build_master(tmp / "unassigned.nxs", _fixture_events())
    summary = hc.convert_campaign(master, read_path=_read_path(FIXTURE / "raw"))
    assert summary.written == [CONTAINER]
    return hc.shots_dir(master) / CONTAINER


def _contract_scope(path: str) -> bool:
    excluded = (*NOT_CONTRACT, *CONTAINER_OWN)
    if path.endswith("/file_metadata/sha256"):  # DAMNIT's: the members' sha256
        return False
    return not any(path == p or path.startswith(p + "/") for p in excluded)


def test_the_excluded_nodes_are_exactly_the_mapping_rows():
    contract = contract_nodes(_manifest())
    assert contract_nodes(_manifest()).keys() >= MAPPING_ROW_NODES
    for alias in MAPPING_ROW_ALIASES:
        assert contract[alias]["link"] in MAPPING_ROW_NODES


def test_the_container_matches_the_manifest_contract(reference_container):
    expected = {
        path: {
            key: node[key] for key in (*MANIFEST_KEYS, "default", "link") if key in node
        }
        for path, node in contract_nodes(_manifest()).items()
        if path not in MAPPING_ROW_NODES
    }
    for alias in MAPPING_ROW_ALIASES:
        expected[alias].pop("link")
    nodes, _ = walk(reference_container)
    written = {
        path: {
            key: node[key] for key in (*MANIFEST_KEYS, "default", "link") if key in node
        }
        for path, node in nodes.items()
        if _contract_scope(path)
    }
    assert len(expected) > 90  # the three instruments, not an empty match
    assert sorted(written) == sorted(expected)
    for path, node in expected.items():
        # walk() reports a missing units attribute as None, as the manifest does.
        assert written[path] == node, path


def _as_pack_reference(node: dict, detector: str) -> dict:
    """A container node as the pack wrote it under ``/entry/detector``."""
    node = copy.deepcopy(node)
    attrs = node.pop("attrs", {})
    attrs.pop("detector_name", None)  # compose_shot's, not the pack's
    if "target" in attrs:
        attrs["target"] = "/entry/detector" + attrs["target"].removeprefix(detector)
    if attrs:
        node["attrs"] = attrs
    if "link" in node:
        node["link"] = "/entry/detector" + node["link"].removeprefix(detector)
    return node


def test_every_detector_subtree_equals_the_pack_reference(reference_container):
    nodes, _ = walk(reference_container)
    for pack, detector in DETECTORS.items():
        reference = json.loads(
            (FIXTURE / "packs" / f"{pack}.json").read_text(encoding="utf-8")
        )
        rebased = {}
        for path, node in nodes.items():
            if path != detector and not path.startswith(detector + "/"):
                continue
            relative = path.removeprefix(detector)
            if relative.lstrip("/").split("/")[0] in AROUND_THE_PACK:
                continue
            rebased["/entry/detector" + relative] = _as_pack_reference(node, detector)
        expected = {p: n for p, n in reference["nodes"].items() if p != "/entry"}
        assert sorted(rebased) == sorted(expected), pack
        for path, node in expected.items():
            assert rebased[path] == node, (pack, path)


def test_the_container_names_its_shot(reference_container):
    with h5py.File(reference_container, "r") as handle:
        assert handle.attrs["NX_class"] == "NXroot"
        assert handle.attrs["default"] == "entry"
        assert handle.attrs["damnit_container_profile"] == hc.CONTAINER_PROFILE
        assert handle.attrs["shot_key"] == "unassigned:20251201:001042"
        entry = handle["entry"]
        assert entry.attrs["NX_class"] == "NXentry"
        assert entry.attrs["shot_key"] == "unassigned:20251201:001042"
        assert int(entry.attrs["shot_number"]) == 1042
        assert entry["title"].asstr()[()] == "unassigned 2025-12-01 shot 1042"
        assert entry["start_time"].asstr()[()].startswith("2025-12-01T14:59:04")
        assert entry["entry_identifier"].asstr()[()] == "20251201_001042"
        # Decision 6: no entry-level plot, each detector keeps its own.
        assert "data" not in entry
        assert "default" not in entry.attrs
        probe = entry["Probe_135_deg/pco_Camera"]
        assert probe["local_name"].asstr()[()] == "Probe135"
        assert probe.attrs["detector_name"] == "pco Camera"
        notes = probe["file_metadata"]
        assert notes["file_path"].asstr()[()] == (
            f"{RECORDED_ROOT}/Probe135/20cm_6kv_00001.tif"
        )
        assert notes["sidecar_files"].asstr()[()] == "20cm_6kv_00002.tif"
        assert notes["file_creation_date"].asstr()[()] == (
            "2025-12-01T14:59:04.300000+00:00"
        )
        assert notes["time_source"].asstr()[()] == "first_seen"
        assert notes["sha256"].asstr()[()] == (
            "20cm_6kv_00001.tif "
            "763002f1da21c8dc8f64b9bc38c32e0e58fd731b3a57c3f0993b05e74292a632\n"
            "20cm_6kv_00002.tif "
            "f0863b6a231804f40da4219daaeda57d717eb713f94c33f7bcb81bb578edba01"
        )
        assert probe["timing_role"].asstr()[()] == "on_shot"
        assert entry["Probe_135_deg/name"].asstr()[()] == "Probe 135 deg"
        assert entry["Reflected_light_spectroscopy/name"].asstr()[()] == (
            "Reflected-light spectroscopy"
        )


def test_a_clean_shot_records_no_problems(reference_container):
    with h5py.File(reference_container, "r") as handle:
        assert "conversion_problems" not in handle["entry"]


# ---------------------------------------------------------------------------
# Grouping, naming.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pack", "name", "expected"),
    [
        # A camera frame and its CSV fall to the same stem.
        (
            "camera_png_csv",
            "set1_2025-12-01_15h-59m-04s_2_original.png",
            ("set1_2025-12-01_15h-59m-04s_2", "set1", 2),
        ),
        (
            "camera_png_csv",
            "set1_2025-12-01_15h-59m-04s_2.csv",
            ("set1_2025-12-01_15h-59m-04s_2", "set1", 2),
        ),
        # A recording named by its conditions: one key for every frame.
        ("sequence_frames", "20cm_6kv_00001.tif", ("20cm_6kv", "20cm_6kv", None)),
        ("sequence_frames", "20cm_6kv_00002.tif", ("20cm_6kv", "20cm_6kv", None)),
        ("sequence_frames", "20cm_6kv_00001.tif.rec", ("20cm_6kv", "20cm_6kv", None)),
        # Set-and-ordinal frames are one acquisition each.
        ("sequence_frames", "set10_00001.tif", ("set10_00001", "set10", 1)),
        ("sequence_frames", "set10_00002.tif", ("set10_00002", "set10", 2)),
        ("sequence_frames", "set10_00001.tif.rec", ("set10_00001", "set10", 1)),
        # The stamped Irr8 dialect carries no ordinal; the plain one does.
        (
            "spectrometer_irr8",
            "7519372SP_01Dez25_155904_0001.Irr8.txt",
            ("|7519372SP_01Dez25_155904_0001", "", None),
        ),
        (
            "spectrometer_irr8",
            "7519372SP_0064.Irr8.txt",
            ("|7519372SP_0064", "", 64),
        ),
        # A name no pattern fits is still the producer's acquisition.
        ("sequence_frames", "focus.tif", ("focus", "", None)),
    ],
)
def test_the_acquisition_key_follows_the_pack_name_patterns(pack, name, expected):
    assert hc.claim(pack, name) == expected


def test_the_spectrometer_folder_label_comes_from_below_the_watch_path():
    assert hc.claim("spectrometer_irr8", "7519372SP_0064.Irr8.txt", "set2") == (
        "set2|7519372SP_0064",
        "set2",
        64,
    )


@pytest.mark.parametrize(
    ("shot_key", "name"),
    [
        ("unassigned:20251201:001042", "20251201_001042.nxs"),
        ("2025_12 Ions:20251203:000007", "20251203_000007.nxs"),
        # A campaign id with colons in it: the date and number are the last two.
        ("a:b:c:20251203:123456", "20251203_123456.nxs"),
        ("exp:unknown:000003", "unknown_000003.nxs"),
    ],
)
def test_container_names_are_date_and_number_and_safe_everywhere(shot_key, name):
    assert hc.container_name(shot_key) == name
    assert not set(name) & set('<>:"/\\|?*')


@pytest.mark.parametrize("bad", ["exp:2025-12-01:000001", "exp:20251201:12a", "nokey"])
def test_a_malformed_shot_key_is_refused(bad):
    with pytest.raises(ValueError, match="shot_key"):
        hc.container_name(bad)


def test_a_second_acquisition_of_one_instrument_gets_its_own_detector(tmp_path):
    raw = tmp_path / "raw"
    events = _shot_events(1042, raw)
    camera = next(
        e for e in events if e["metadata"]["instrument"]["id"] == "m1_spec_fib_cer"
    )
    second = copy.deepcopy(camera)
    second["event_id"] += "-seq7"
    for item in [second["payload_ref"], *second["payload_ref"]["members"]]:
        old = item["path"]
        item["path"] = old.replace("_2.csv", "_7.csv").replace(
            "_2_original", "_7_original"
        )
        local = raw / item["path"].removeprefix(RECORDED_ROOT + "/")
        local.write_bytes((raw / old.removeprefix(RECORDED_ROOT + "/")).read_bytes())
    master = _build_master(tmp_path / "out" / "unassigned.nxs", [*events, second])
    hc.convert_campaign(master, read_path=_read_path(raw))
    with h5py.File(hc.shots_dir(master) / CONTAINER, "r") as handle:
        family = handle["entry/Reflected_light_spectroscopy"]
        assert {
            "_515_Reflected_Light_Spectrometer",
            "_515_Reflected_Light_Spectrometer_7",
        } <= set(family)


def test_the_timing_role_falls_back_to_the_catalogue(tmp_path):
    raw = tmp_path / "raw"
    events = _shot_events(1042, raw)
    for event in events:
        event["metadata"]["instrument"].pop("timing_role")
    master = _build_master(tmp_path / "out" / "unassigned.nxs", events)
    hc.convert_campaign(master, read_path=_read_path(raw))
    with h5py.File(hc.shots_dir(master) / CONTAINER, "r") as handle:
        role = handle["entry/Probe_135_deg/pco_Camera/timing_role"]
        assert role.asstr()[()] == "on_shot"


CASES = FIXTURE / "packs" / "cases"


def _frame_event(name: str, number: int, raw: Path, source: Path, **meta) -> dict:
    """One Probe135 file as planet-watchdog sends it, attributed to shot `number`."""
    template = next(
        e for e in _fixture_events() if e["metadata"]["instrument"]["id"] == "probe135"
    )
    event = copy.deepcopy(template)
    (raw / "Probe135").mkdir(parents=True, exist_ok=True)
    (raw / "Probe135" / name).write_bytes(source.read_bytes())
    event["event_id"] = f"probe-{number}-{name}"
    event["shot_number"] = number
    event["shot_id"] = f"shot-{number:06d}"
    for trigger in event["metadata"]["zmq_data"]:
        trigger["payload"]["shot_number"] = number
    event["metadata"]["acquisition"].update(meta)
    event["payload_ref"].update(
        path=f"{RECORDED_ROOT}/Probe135/{name}",
        filename=name,
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    return event


FRAME_1 = FIXTURE / "raw" / "Probe135" / "20cm_6kv_00001.tif"
FRAME_2 = FIXTURE / "raw" / "Probe135" / "20cm_6kv_00002.tif"


def test_a_recording_whose_cadence_spans_two_shots_splits_by_shot(tmp_path):
    """Frames 1-2 belong to shot 1042, 3-4 to 1043: one stack per shot."""
    raw = tmp_path / "raw"
    events = [
        _frame_event(f"20cm_6kv_{n:05d}.tif", shot, raw, frame)
        for n, shot, frame in (
            (1, 1042, FRAME_1),
            (2, 1042, FRAME_2),
            (3, 1043, FRAME_1),
            (4, 1043, FRAME_2),
        )
    ]
    master = _build_master(tmp_path / "out" / "unassigned.nxs", events)
    hc.convert_campaign(master, read_path=_read_path(raw))
    for name, frames in (
        ("20251201_001042.nxs", [1, 2]),
        ("20251201_001043.nxs", [3, 4]),
    ):
        with h5py.File(hc.shots_dir(master) / name, "r") as handle:
            detector = handle["entry/Probe_135_deg/pco_Camera"]
            assert detector["raw_data/frame_number"][()].tolist() == frames
            assert detector["data/image"].shape == (2, 4, 5)


def test_a_rec_sent_as_its_own_event_joins_its_recording(tmp_path):
    raw = tmp_path / "raw"
    rec = CASES / "sequence_frames__recording_with_comment" / "20cm_6kv_00001.tif.rec"
    events = [
        _frame_event("20cm_6kv_00001.tif", 1042, raw, FRAME_1),
        _frame_event("20cm_6kv_00002.tif", 1042, raw, FRAME_2),
        _frame_event("20cm_6kv_00001.tif.rec", 1042, raw, rec),
    ]
    master = _build_master(tmp_path / "out" / "unassigned.nxs", events)
    hc.convert_campaign(master, read_path=_read_path(raw))
    with h5py.File(hc.shots_dir(master) / CONTAINER, "r") as handle:
        instrument = handle["entry/Probe_135_deg"]
        assert isinstance(instrument, h5py.Group)
        assert sorted(k for k in instrument if k != "name") == ["pco_Camera"]
        detector = instrument["pco_Camera"]
        assert "recorder_comment" in detector["original_metadata"]
        assert detector["data/image"].shape == (2, 4, 5)


def test_frames_with_a_label_but_no_quantity_still_stack(tmp_path):
    """Differs from shot-aligner, which leaves `focus_00001.tif` unparsed.

    The producer attributed both frames to this shot, so DAMNIT keeps them, as
    one recording of the `focus` label (design note, section 3).
    """
    raw = tmp_path / "raw"
    events = [
        _frame_event("focus_00001.tif", 1042, raw, FRAME_1),
        _frame_event("focus_00002.tif", 1042, raw, FRAME_2),
    ]
    assert hc.claim("sequence_frames", "focus_00001.tif") == ("focus", "focus", None)
    master = _build_master(tmp_path / "out" / "unassigned.nxs", events)
    hc.convert_campaign(master, read_path=_read_path(raw))
    with h5py.File(hc.shots_dir(master) / CONTAINER, "r") as handle:
        assert handle["entry/Probe_135_deg/pco_Camera/data/image"].shape == (2, 4, 5)


def test_second_acquisitions_are_named_by_time_then_by_their_stem(tmp_path):
    """The earliest keeps the plain name; the rest take their sanitised stem.

    Neither the order the events arrived in nor the order of the master's
    table changes which file is which detector.
    """
    raw = tmp_path / "raw"
    events = [
        _frame_event("beam.tif", 1042, raw, FRAME_1, time="2025-12-01T14:59:04+00:00"),
        _frame_event(
            "dark-2.tif", 1042, raw, FRAME_2, time="2025-12-01T14:59:01+00:00"
        ),
    ]
    names = []
    for order in (events, events[::-1]):
        master = _build_master(tmp_path / f"out{len(names)}" / "unassigned.nxs", order)
        hc.convert_campaign(master, read_path=_read_path(raw))
        with h5py.File(hc.shots_dir(master) / CONTAINER, "r") as handle:
            instrument = handle["entry/Probe_135_deg"]
            assert isinstance(instrument, h5py.Group)
            names.append({
                k: instrument[k]["file_metadata/file_name"].asstr()[()]
                for k in instrument
                if k != "name"
            })
    assert (
        names[0]
        == names[1]
        == {
            "pco_Camera": "dark-2.tif",
            "pco_Camera_beam": "beam.tif",
        }
    )


def test_the_master_is_read_in_slices_keeping_only_acquisitions(campaign, monkeypatch):
    whole = hc.read_master(campaign["master"])
    monkeypatch.setattr(hc, "READ_SLICE", 3)
    sliced = hc.read_master(campaign["master"])
    assert sliced == whole
    _, _, events = whole
    assert len(events) == 16  # every event of the four shots is an acquisition
    for event in events:
        # Only what a container is written from: no trigger payloads.
        assert set(event["metadata"]) <= {"instrument", "watch", "acquisition"}
        assert "zmq_topic" not in event["payload_ref"]


def test_events_without_an_instrument_format_are_not_kept(tmp_path):
    raw = tmp_path / "raw"
    events = _shot_events(1042, raw)
    trigger = copy.deepcopy(events[0])
    trigger["event_id"] = "trigger-1042"
    trigger["metadata"] = {"trigger": {"role": "main"}}
    master = _build_master(tmp_path / "out" / "unassigned.nxs", [*events, trigger])
    _, _, kept = hc.read_master(master)
    assert "trigger-1042" not in {e["event_id"] for e in kept}
    assert len(kept) == 4


def test_a_format_without_a_pack_is_recorded_not_written(tmp_path):
    raw = tmp_path / "raw"
    events = _shot_events(1042, raw)
    odd = copy.deepcopy(events[1])
    odd["event_id"] += "-odd"
    odd["metadata"]["instrument"] = {"id": "tps90", "format": "tps_parabola"}
    master = _build_master(tmp_path / "out" / "unassigned.nxs", [*events, odd])
    hc.convert_campaign(master, read_path=_read_path(raw))
    with h5py.File(hc.shots_dir(master) / CONTAINER, "r") as handle:
        problems = handle["entry/conversion_problems/data"].asstr()[()]
    assert "no pack writes 'tps_parabola'" in problems


def test_an_instrument_outside_the_catalogue_keeps_its_own_name(tmp_path):
    raw = tmp_path / "raw"
    events = _shot_events(1042, raw)
    for event in events:
        if event["metadata"]["instrument"]["id"] == "reflected_515_spectrometer":
            event["metadata"]["instrument"]["id"] = "new_spectrometer"
    master = _build_master(tmp_path / "out" / "unassigned.nxs", events)
    hc.convert_campaign(master, read_path=_read_path(raw))
    with h5py.File(hc.shots_dir(master) / CONTAINER, "r") as handle:
        entry = handle["entry"]
        # Its watch name is the instrument; nothing says which family it is in.
        detector = entry["Reflected_515_spectrometer/detector"]
        assert detector.attrs["NX_class"] == "NXdetector"
        assert detector.attrs["default"] == "data"
        problems = entry["conversion_problems/data"].asstr()[()]
    assert "new_spectrometer is not in the instrument catalogue" in problems


# ---------------------------------------------------------------------------
# Incremental and resumable.
# ---------------------------------------------------------------------------


def test_a_second_run_rewrites_nothing(campaign):
    read_path = _read_path(campaign["raw"])
    first = hc.convert_campaign(campaign["master"], read_path=read_path)
    assert len(first.written) == 4
    shots = hc.shots_dir(campaign["master"])
    before = _mtimes(shots)
    second = hc.convert_campaign(campaign["master"], read_path=read_path)
    assert second.written == []
    assert sorted(second.skipped) == sorted(before)
    assert _mtimes(shots) == before
    manifest = json.loads((shots / hc.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert set(manifest["containers"]) == set(before)
    for name, record in manifest["containers"].items():
        with h5py.File(shots / name, "r") as handle:
            assert handle.attrs["damnit_input_fingerprint"] == record["fingerprint"]


def test_changing_one_raws_bytes_rewrites_only_that_shot(campaign):
    read_path = _read_path(campaign["raw"])
    hc.convert_campaign(campaign["master"], read_path=read_path)
    shots = hc.shots_dir(campaign["master"])
    before = _mtimes(shots)

    # The camera of shot 1044 is re-exported; watchdog re-sends its event with
    # the new bytes' sha256, and the next build publishes it.
    events = copy.deepcopy(campaign["events"])
    csv = (
        campaign["raw"]
        / "1044"
        / "M1_Spec_Fib_Cer"
        / "set1_2025-12-01_15h-59m-04s_2.csv"
    )
    csv.write_bytes(csv.read_bytes().replace(b"Comment", b"Remark ", 1))
    digest = hashlib.sha256(csv.read_bytes()).hexdigest()
    for event in events:
        for member in event["payload_ref"].get("members") or []:
            if member["path"].endswith("/1044/M1_Spec_Fib_Cer/" + csv.name):
                member["sha256"] = digest
    _build_master(campaign["master"], events)

    run = hc.convert_campaign(campaign["master"], read_path=read_path)
    assert run.written == ["20251201_001044.nxs"]
    after = _mtimes(shots)
    assert {n for n in after if after[n] != before[n]} == {"20251201_001044.nxs"}


def test_a_code_or_catalogue_change_rebuilds_every_container(campaign, monkeypatch):
    read_path = _read_path(campaign["raw"])
    hc.convert_campaign(campaign["master"], read_path=read_path)
    monkeypatch.setattr(hc, "code_digest", lambda: "a different converter")
    assert (
        len(hc.convert_campaign(campaign["master"], read_path=read_path).written) == 4
    )


def test_a_crash_after_k_shots_resumes_with_the_rest(campaign):
    read_path = _read_path(campaign["raw"])
    shots = hc.shots_dir(campaign["master"])

    class KilledError(Exception):
        pass

    done: list[str] = []

    def die_after_two(name: str) -> None:
        done.append(name)
        if len(done) == 2:
            # The process dies mid-way through the third: its temp file is
            # left behind, and the manifest was never flushed.
            (shots / "20251201_001044.nxs.tmp").write_bytes(b"half a file")
            raise KilledError

    with pytest.raises(KilledError):
        hc.convert_campaign(
            campaign["master"],
            read_path=read_path,
            after_write=die_after_two,
            flush_seconds=3600,
        )
    assert sorted(p.name for p in shots.glob("*.nxs")) == sorted(done)
    assert not (shots / hc.MANIFEST_NAME).exists()
    finished = _mtimes(shots)

    resumed = hc.convert_campaign(campaign["master"], read_path=read_path)
    assert sorted(resumed.skipped) == sorted(done)  # adopted by their fingerprint
    assert len(resumed.written) == 2
    assert {n: t for n, t in _mtimes(shots).items() if n in finished} == finished
    assert not list(shots.glob("*.tmp"))
    assert len(_mtimes(shots)) == 4


def test_a_different_path_map_rebuilds_every_container(campaign):
    raw = campaign["raw"]
    hc.convert_campaign(campaign["master"], read_path=_read_path(raw))
    # The same mapping, spelled with an extra rule: another configuration.
    other = hc.make_read_path(
        f"{RECORDED_ROOT}={raw.as_posix()},Z:/unused={raw.as_posix()}"
    )
    assert len(hc.convert_campaign(campaign["master"], read_path=other).written) == 4


def test_the_fingerprint_names_the_libraries_that_wrote_it():
    import h5py as h5
    import PIL

    versions = hc.library_versions()
    assert versions == {
        "h5py": h5.__version__,
        "hdf5": h5.version.hdf5_version,
        "pillow": PIL.__version__,
    }


def test_containers_are_renamed_into_place_whole(campaign, monkeypatch):
    """A failure while writing leaves the previous container, never half of one."""
    read_path = _read_path(campaign["raw"])
    hc.convert_campaign(campaign["master"], read_path=read_path)
    shots = hc.shots_dir(campaign["master"])
    before = (shots / CONTAINER).read_bytes()

    def broken(*args, **kwargs):
        message = "disk full"
        raise OSError(message)

    monkeypatch.setattr(hc, "code_digest", lambda: "forces a rewrite")
    monkeypatch.setattr(hc, "_write_shot", broken)
    run = hc.convert_campaign(campaign["master"], read_path=read_path)
    assert len(run.failed) == 4
    assert (shots / CONTAINER).read_bytes() == before
    assert not list(shots.glob("*.tmp"))


def test_one_failing_container_does_not_stop_the_pass(campaign, monkeypatch):
    """Regression: a PermissionError on the first shot wrote none of the rest."""
    read_path = _read_path(campaign["raw"])
    real = hc.write_container

    def refuse_the_first(target, *args, **kwargs):
        if target.name == CONTAINER:
            message = "Access is denied"
            raise PermissionError(message)
        return real(target, *args, **kwargs)

    monkeypatch.setattr(hc, "write_container", refuse_the_first)
    run = hc.convert_campaign(campaign["master"], read_path=read_path)
    assert run.failed == [CONTAINER]
    assert len(run.written) == 3
    shots = hc.shots_dir(campaign["master"])
    record = json.loads((shots / hc.MANIFEST_NAME).read_text())["containers"][CONTAINER]
    assert "PermissionError: Access is denied" in record["error"]

    monkeypatch.setattr(hc, "write_container", real)
    assert hc.convert_campaign(campaign["master"], read_path=read_path).written == [
        CONTAINER
    ]


def test_a_rename_refused_while_the_container_is_open_is_retried(campaign, monkeypatch):
    from damnit_api.metadata import hzdr_nexus

    monkeypatch.setattr(hzdr_nexus, "REPLACE_DELAY_S", 0)
    refused = []
    real = Path.replace

    def busy_once(self, other):
        if str(self).endswith(".nxs.tmp") and not refused:
            refused.append(self)
            message = "held open by a viewer"
            raise PermissionError(message)
        return real(self, other)

    monkeypatch.setattr(Path, "replace", busy_once)
    run = hc.convert_campaign(campaign["master"], read_path=_read_path(campaign["raw"]))
    assert refused
    assert len(run.written) == 4
    assert run.failed == []


def test_containers_are_fsynced_before_the_rename(campaign, monkeypatch):
    synced = []
    real = os.fsync
    monkeypatch.setattr(hc.os, "fsync", lambda fd: (synced.append(fd), real(fd)))
    hc.convert_campaign(campaign["master"], read_path=_read_path(campaign["raw"]))
    assert len(synced) >= 4


@pytest.mark.parametrize(
    ("family", "instrument_name", "where"),
    [
        # A family that sanitises to an entry-level field of the writer's.
        ("title", "Spec", "title_instrument/Spec"),
        ("conversion problems", "Spec", "conversion_problems_instrument/Spec"),
        # A detector that sanitises to its NXinstrument's `name` field.
        ("Optics", "name", "Optics/name_detector"),
    ],
)
def test_reserved_names_are_never_written_over(
    tmp_path, monkeypatch, family, instrument_name, where
):
    from damnit_api.metadata.hzdr_packs import vendor

    catalogue = dict(vendor.catalogue())
    catalogue["reflected_515_spectrometer"] = {
        **catalogue["reflected_515_spectrometer"],
        "family": family,
        "instrument_name": instrument_name,
    }
    monkeypatch.setattr(vendor, "catalogue", lambda: catalogue)
    raw = tmp_path / "raw"
    master = _build_master(tmp_path / "out" / "unassigned.nxs", _shot_events(1042, raw))
    run = hc.convert_campaign(master, read_path=_read_path(raw))
    assert run.written == [CONTAINER]
    with h5py.File(hc.shots_dir(master) / CONTAINER, "r") as handle:
        entry = handle["entry"]
        assert entry[where].attrs["NX_class"] == "NXdetector"
        assert entry["title"].asstr()[()] == "unassigned 2025-12-01 shot 1042"


def test_the_manifest_is_flushed_as_the_run_goes(campaign):
    shots = hc.shots_dir(campaign["master"])
    seen: list[set[str]] = []

    def look(name: str) -> None:
        manifest = shots / hc.MANIFEST_NAME
        if manifest.exists():
            seen.append(set(json.loads(manifest.read_text())["containers"]))

    hc.convert_campaign(
        campaign["master"],
        read_path=_read_path(campaign["raw"]),
        after_write=look,
        flush_seconds=0,
    )
    # Before the 2nd container's hook, the 1st was already on disk in the manifest.
    assert seen
    assert seen[0] == {"20251201_001042.nxs"}


def test_a_producer_without_sha256_is_fingerprinted_by_stat(tmp_path):
    raw = tmp_path / "raw"
    events = _shot_events(1042, raw)
    for event in events:
        event["payload_ref"]["sha256"] = None
        for member in event["payload_ref"].get("members") or []:
            member["sha256"] = None
    master = _build_master(tmp_path / "out" / "unassigned.nxs", events)
    read_path = _read_path(raw)
    hc.convert_campaign(master, read_path=read_path)
    assert hc.convert_campaign(master, read_path=read_path).written == []
    spectrum = next(raw.rglob("*.Irr8.txt"))
    stat = spectrum.stat()
    os.utime(spectrum, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    assert hc.convert_campaign(master, read_path=read_path).written == [CONTAINER]


def test_the_labfrog_row_is_carried_beside_the_detectors(tmp_path):
    shots = [
        {
            "shot_key": "c1:20251201:001042",
            "fired_at": "2025-12-01T14:59:04+00:00",
            "record_id": "lf-77",
            "labfrog_date_time": "2025-12-01 15:59:03",
            "labfrog_local_count": 12,
        }
    ]
    events = [
        {
            "event_id": "e1",
            "shot_key": "c1:20251201:001042",
            "metadata_json": json.dumps({
                "instrument": {"id": "x", "format": "camera_png_csv"}
            }),
            "payload_ref_json": json.dumps({"path": f"{RECORDED_ROOT}/gone.png"}),
        }
    ]
    (plan,) = hc.plan_shots("c1", shots, events)
    target = tmp_path / plan.name
    hc.write_container(target, plan, Path, "fp")
    with h5py.File(target, "r") as handle:
        labfrog = handle["entry/labfrog_shot"]
        assert labfrog.attrs["NX_class"] == "NXcollection"
        assert labfrog["record_id"].asstr()[()] == "lf-77"
        assert labfrog["date_time"].asstr()[()] == "2025-12-01 15:59:03"
        assert int(labfrog["local_count"][()]) == 12
        assert handle["entry/title"].asstr()[()] == "c1 2025-12-01 shot 1042"
        problems = handle["entry/conversion_problems/data"].asstr()[()]
    assert f"missing file {RECORDED_ROOT}/gone.png" in problems


def test_a_pack_that_raises_costs_its_detector_not_the_shot(campaign, monkeypatch):
    def explode(group, acquisition, read_path):
        message = "reader bug"
        raise ValueError(message)

    monkeypatch.setitem(
        hc.hzdr_packs.PACKS,
        "spectrometer_irr8",
        type("Broken", (), {"write": staticmethod(explode)}),
    )
    run = hc.convert_campaign(campaign["master"], read_path=_read_path(campaign["raw"]))
    assert len(run.written) == 4
    with h5py.File(hc.shots_dir(campaign["master"]) / CONTAINER, "r") as handle:
        family = handle["entry/Reflected_light_spectroscopy"]
        assert "Reflected_515_Spectrometer" not in family
        problems = handle["entry/conversion_problems/data"].asstr()[()]
    assert "conversion failed: ValueError: reader bug" in problems


# ---------------------------------------------------------------------------
# Missing and unreadable files.
# ---------------------------------------------------------------------------


def test_a_missing_file_drops_its_detector_and_says_why(campaign):
    read_path = _read_path(campaign["raw"])
    frames = sorted((campaign["raw"] / "1043" / "Probe135").glob("*.tif"))
    held = {f: f.read_bytes() for f in frames}
    for frame in frames:
        frame.unlink()

    run = hc.convert_campaign(campaign["master"], read_path=read_path)
    assert len(run.written) == 4  # the build does not fail
    shots = hc.shots_dir(campaign["master"])
    with h5py.File(shots / "20251201_001043.nxs", "r") as handle:
        entry = handle["entry"]
        assert "Probe_135_deg" not in entry  # the empty NXinstrument goes too
        assert "Reflected_light_spectroscopy" in entry
        problems = entry["conversion_problems/data"].asstr()[()]
        assert entry["conversion_problems"].attrs["NX_class"] == "NXnote"
    assert f"missing file {RECORDED_ROOT}/1043/Probe135/20cm_6kv_00001.tif" in problems
    assert "Probe135: detector omitted" in problems
    manifest = json.loads((shots / hc.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert len(manifest["containers"]["20251201_001043.nxs"]["missing"]) == 2

    # Still missing: nothing to do. Back on the share: that shot is redone.
    assert hc.convert_campaign(campaign["master"], read_path=read_path).written == []
    for frame, data in held.items():
        frame.write_bytes(data)
    rerun = hc.convert_campaign(campaign["master"], read_path=read_path)
    assert rerun.written == ["20251201_001043.nxs"]
    with h5py.File(shots / "20251201_001043.nxs", "r") as handle:
        assert handle["entry/Probe_135_deg/pco_Camera/data/image"].shape == (2, 4, 5)
        assert "conversion_problems" not in handle["entry"]


def test_a_file_back_after_a_crash_lost_the_manifest_is_reconverted(campaign):
    """Regression: the missing-file retry lived only in the manifest."""
    read_path = _read_path(campaign["raw"])
    spectrum = next((campaign["raw"] / "1044").rglob("*.Irr8.txt"))
    held = spectrum.read_bytes()
    spectrum.unlink()
    hc.convert_campaign(campaign["master"], read_path=read_path)
    shots = hc.shots_dir(campaign["master"])
    (shots / hc.MANIFEST_NAME).unlink()  # the crash, before any flush
    spectrum.write_bytes(held)

    run = hc.convert_campaign(campaign["master"], read_path=read_path)
    assert run.written == ["20251201_001044.nxs"]
    with h5py.File(shots / "20251201_001044.nxs", "r") as handle:
        assert (
            "Reflected_515_Spectrometer" in handle["entry/Reflected_light_spectroscopy"]
        )


def test_a_file_changed_on_disk_under_the_same_sha256_is_reconverted(campaign):
    """Design (b): the producer's sha256 is trusted, but size/mtime are watched."""
    read_path = _read_path(campaign["raw"])
    hc.convert_campaign(campaign["master"], read_path=read_path)
    spectrum = next((campaign["raw"] / "1043").rglob("*.Irr8.txt"))
    stat = spectrum.stat()
    os.utime(spectrum, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5 * 10**9))

    run = hc.convert_campaign(campaign["master"], read_path=read_path)
    assert run.written == ["20251201_001043.nxs"]
    shots = hc.shots_dir(campaign["master"])
    with h5py.File(shots / "20251201_001043.nxs", "r") as handle:
        problems = handle["entry/conversion_problems/data"].asstr()[()]
    assert "changed on disk since it was last converted" in problems
    assert spectrum.name in problems
    # The new size and mtime are the reference now.
    assert hc.convert_campaign(campaign["master"], read_path=read_path).written == []


def test_an_unreadable_file_is_recorded_and_not_retried(campaign):
    read_path = _read_path(campaign["raw"])
    spectrum = next((campaign["raw"] / "1045").rglob("*.Irr8.txt"))
    spectrum.write_text("not an Irr8 file\n", encoding="utf-8")

    hc.convert_campaign(campaign["master"], read_path=read_path)
    shots = hc.shots_dir(campaign["master"])
    with h5py.File(shots / "20251201_001045.nxs", "r") as handle:
        family = handle["entry/Reflected_light_spectroscopy"]
        assert "Reflected_515_Spectrometer" not in family
        assert "_515_Reflected_Light_Spectrometer" in family
        problems = handle["entry/conversion_problems/data"].asstr()[()]
    assert "could not read" in problems
    assert "Reflected 515 spectrometer: detector omitted" in problems
    assert hc.convert_campaign(campaign["master"], read_path=read_path).written == []


# ---------------------------------------------------------------------------
# The worker's lock, outside the campaign lock.
# ---------------------------------------------------------------------------


def test_the_master_build_runs_while_conversion_is_in_progress(campaign):
    master = campaign["master"]
    shots = hc.shots_dir(master)
    rebuilt: list[str] = []

    def publish_the_master(name: str) -> None:
        if rebuilt:
            return
        # Conversion holds its own lock, and not the master's...
        assert (shots / ".convert.lock").is_file()
        assert not master.with_name(master.name + ".lock").exists()
        # ...nor the master file: the builder takes its lock and renames a new
        # master into place mid-conversion.
        _build_master(master, campaign["events"])
        rebuilt.append(name)

    runs = hc.run_conversion(
        master, read_path=_read_path(campaign["raw"]), after_write=publish_the_master
    )
    assert rebuilt
    # The master changed under it, so it looked again; nothing new to write.
    assert len(runs) == 2
    assert len(runs[0].written) == 4
    assert runs[1].written == []
    assert not (shots / ".convert.lock").exists()


def test_a_second_worker_leaves_a_request_for_the_one_running(campaign):
    master = campaign["master"]
    shots = hc.shots_dir(master)
    shots.mkdir(parents=True)
    with single_writer_lock(shots / ".convert"):
        assert hc.run_conversion(master, read_path=_read_path(campaign["raw"])) == []
        assert (shots / hc.PENDING_NAME).exists()
    assert not list(shots.glob("*.nxs"))


def test_a_request_arriving_mid_pass_gets_another_pass(campaign):
    master = campaign["master"]
    shots = hc.shots_dir(master)
    asked: list[str] = []

    def another_worker_arrives(name: str) -> None:
        if asked:
            return
        asked.append(name)
        # A second worker, finding the lock taken, leaves its request.
        (shots / hc.PENDING_NAME).touch()

    runs = hc.run_conversion(
        master,
        read_path=_read_path(campaign["raw"]),
        after_write=another_worker_arrives,
    )
    assert len(runs) == 2
    assert runs[1].written == []
    assert not (shots / hc.PENDING_NAME).exists()


def test_a_dead_workers_lock_from_another_host_is_reclaimed_when_stale(campaign):
    """A rebooted or foreign host's PID cannot be checked: age decides."""
    master = campaign["master"]
    shots = hc.shots_dir(master)
    shots.mkdir(parents=True)
    lock = shots / ".convert.lock"
    lock.write_text("worker-pc-2:4242:99", encoding="utf-8")
    read_path = _read_path(campaign["raw"])
    assert hc.run_conversion(master, read_path=read_path) == []  # fresh: held
    old = time.time() - hc.LOCK_STALE_AFTER_S - 60
    os.utime(lock, (old, old))
    runs = hc.run_conversion(master, read_path=read_path)
    assert len(runs[0].written) == 4


def test_the_conversion_lock_is_refreshed_once_per_container(campaign):
    master = campaign["master"]
    lock = hc.shots_dir(master) / ".convert.lock"
    ages: list[float] = []

    def age_then_look(name: str) -> None:
        ages.append(time.time() - lock.stat().st_mtime)
        old = time.time() - 600
        os.utime(lock, (old, old))

    hc.run_conversion(
        master, read_path=_read_path(campaign["raw"]), after_write=age_then_look
    )
    # Aged to ten minutes after every container, fresh again by the next one.
    assert len(ages) == 4
    assert all(age < 60 for age in ages)


def test_a_missing_master_is_nothing_to_convert(tmp_path):
    assert hc.run_conversion(tmp_path / "none.nxs", read_path=Path) == []


# ---------------------------------------------------------------------------
# Memory: one frame, however long the recording.
# ---------------------------------------------------------------------------


def _recording_events(frames: int, raw: Path) -> list[dict]:
    template = next(
        e for e in _fixture_events() if e["metadata"]["instrument"]["id"] == "probe135"
    )
    events = []
    for n in range(1, frames + 1):
        event = copy.deepcopy(template)
        name = f"20cm_6kv_{n:05d}.tif"
        (raw / "Probe135").mkdir(parents=True, exist_ok=True)
        (raw / "Probe135" / name).touch()
        event["event_id"] = f"frame-{n}"
        event["payload_ref"].update(
            path=f"{RECORDED_ROOT}/Probe135/{name}", filename=name, sha256=f"{n:064x}"
        )
        events.append(event)
    return events


def _peak_frames_held(tmp_path: Path, monkeypatch, frames: int) -> int:
    alive: set[int] = set()
    peak = 0

    def read(path: Path):
        nonlocal peak
        number = int(path.stem.rsplit("_", 1)[1])
        frame = np.full((16, 12), number, dtype=np.uint16)
        alive.add(number)
        weakref.finalize(frame, alive.discard, number)
        peak = max(peak, len(alive))
        return frame

    monkeypatch.setattr(sequence_frames, "read_image", read)
    raw = tmp_path / "raw"
    master = _build_master(
        tmp_path / "out" / "unassigned.nxs", _recording_events(frames, raw)
    )
    hc.convert_campaign(master, read_path=_read_path(raw))
    with h5py.File(hc.shots_dir(master) / CONTAINER, "r") as handle:
        stack = handle["entry/Probe_135_deg/pco_Camera/raw_data/image"]
        assert stack.shape == (frames, 16, 12)
        assert stack.chunks == (1, 16, 12)
        assert (stack.compression, stack.compression_opts) == ("gzip", 4)
    return peak


def test_a_long_recording_is_converted_one_frame_at_a_time(tmp_path, monkeypatch):
    short = _peak_frames_held(tmp_path / "a", monkeypatch, 3)
    long = _peak_frames_held(tmp_path / "b", monkeypatch, 200)
    assert long <= 2
    assert long == short


# ---------------------------------------------------------------------------
# The worker CLI.
# ---------------------------------------------------------------------------


def _worker(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DW_API_DAMNIT_PATH": str(Path.cwd())}
    return subprocess.run(  # noqa: S603
        [sys.executable, str(WORKER), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_the_worker_converts_a_campaign(campaign):
    result = _worker(
        "--master",
        str(campaign["master"]),
        "--path-map",
        f"{RECORDED_ROOT}={campaign['raw'].as_posix()}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "4 written" in result.stdout
    assert len(list(hc.shots_dir(campaign["master"]).glob("*.nxs"))) == 4


@pytest.fixture
def output_root(tmp_path):
    raw = tmp_path / "raw"
    root = tmp_path / "out"
    _build_master(root / "_unassigned" / "unassigned.nxs", _shot_events(1042, raw))
    _build_master(root / "c1" / "c1.nxs", _shot_events(1043, raw))
    _build_master(root / "c2" / "c2.nxs", _shot_events(1044, raw))
    (root / "not-a-campaign").mkdir()
    return root, f"{RECORDED_ROOT}={raw.as_posix()}"


def test_the_worker_skips_the_unassigned_bucket_by_default(output_root):
    root, path_map = output_root
    result = _worker("--output-root", str(root), "--path-map", path_map)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (root / "_unassigned" / "shots").exists()
    assert (root / "c1" / "shots" / "20251201_001043.nxs").is_file()
    assert (root / "c2" / "shots" / "20251201_001044.nxs").is_file()


def test_the_worker_converts_the_bucket_when_asked(output_root):
    root, path_map = output_root
    result = _worker(
        "--output-root", str(root), "--path-map", path_map, "--include-unassigned"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "_unassigned" / "shots" / "20251201_001042.nxs").is_file()


def test_the_worker_converts_only_the_campaigns_named(output_root):
    root, path_map = output_root
    result = _worker(
        "--output-root", str(root), "--path-map", path_map, "--campaign", "c2"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (root / "c1" / "shots").exists()
    assert (root / "c2" / "shots" / "20251201_001044.nxs").is_file()


def test_the_worker_reports_a_failed_container_and_exits_nonzero(campaign):
    shots = hc.shots_dir(campaign["master"])
    shots.mkdir(parents=True)
    (shots / CONTAINER).mkdir()  # a directory where the container should go
    result = _worker(
        "--master",
        str(campaign["master"]),
        "--path-map",
        f"{RECORDED_ROOT}={campaign['raw'].as_posix()}",
    )
    assert result.returncode == 1
    assert "3 written" in result.stdout
    assert "1 failed" in result.stdout


def test_the_worker_needs_a_master_or_a_root():
    result = _worker()
    assert result.returncode == 2


# --- A writer whose lock was taken over stops (phase 3 verification, bug a) --


def test_a_writer_whose_lock_was_taken_publishes_nothing_more(campaign):
    """``heartbeat`` failing before a rename ends the pass; nothing is renamed."""
    from damnit_api.metadata.hzdr_nexus import LockLostError

    shots = hc.shots_dir(campaign["master"])
    calls: list[int] = []

    def lost_after_first(*_args) -> None:
        calls.append(1)
        if len(calls) > 2:  # the first container's two checks pass
            message = "taken over"
            raise LockLostError(message)

    with pytest.raises(LockLostError):
        hc.convert_campaign(
            campaign["master"],
            read_path=_read_path(campaign["raw"]),
            heartbeat=lost_after_first,
            nonce="mine",
        )
    assert len(list(shots.glob("*.nxs"))) == 1
    assert list(shots.glob("*.tmp")) == []
    assert not (shots / hc.MANIFEST_NAME).exists()


def test_temp_names_are_the_writers_own_and_earlier_ones_are_cleared(
    campaign, monkeypatch
):
    shots = hc.shots_dir(campaign["master"])
    shots.mkdir(parents=True, exist_ok=True)
    (shots / "20251201_001044.nxs.oldwriter.tmp").write_bytes(b"half a file")
    (shots / "20251201_001044.nxs.tmp").write_bytes(b"half a file")
    temps: list[str] = []
    real = Path.replace

    def record(self, other):
        temps.append(self.name)
        return real(self, other)

    monkeypatch.setattr(Path, "replace", record)
    run = hc.convert_campaign(
        campaign["master"], read_path=_read_path(campaign["raw"]), nonce="n0nce"
    )
    assert len(run.written) == 4
    container_temps = [t for t in temps if t.endswith(".nxs.n0nce.tmp")]
    assert len(container_temps) == 4
    assert list(shots.glob("*.tmp")) == []


def test_run_conversion_writes_under_its_lock_nonce(campaign, monkeypatch):
    temps: list[str] = []
    real = Path.replace

    def record(self, other):
        temps.append(self.name)
        return real(self, other)

    monkeypatch.setattr(Path, "replace", record)
    runs = hc.run_conversion(campaign["master"], read_path=_read_path(campaign["raw"]))
    assert runs
    assert len(runs[0].written) == 4
    container_temps = [t for t in temps if t.startswith("2025") and t.endswith(".tmp")]
    assert len(container_temps) == 4
    assert all(t.count(".") == 3 for t in container_temps), container_temps
