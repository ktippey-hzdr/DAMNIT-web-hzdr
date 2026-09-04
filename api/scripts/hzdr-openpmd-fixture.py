"""Build the synthetic multi-source canonical fixture for openPMD preflight.

Phase 2 of `hzdr/docs/plans/openpmd-projection-plan.md`. Everything here is
synthetic: no broker, no ASAPO endpoint, no real campaign. The point is to get
a *canonical* file - one produced by the same reconciler and single-writer
bridge builder the deployment uses - that contains all four producers at once,
so a reviewed openPMD projection plan can be resolved against real paths
instead of guessed ones.

Three canonical shots plus one orphan:

1. shot 101 - trigger + LabFrog + PLANET Watchdog **PC A** + ASAPO LaserData
2. shot 102 - trigger + LabFrog + PLANET Watchdog **PC B**, no ASAPO event
3. shot 103 - trigger + LabFrog only; the ASAPO and Watchdog records are
   absent and the iteration must survive that
4. an early Watchdog PC A event, three days off and carrying a shot number no
   LabFrog row has, which must stay unmatched and visible

Shot 102's Watchdog event deliberately reuses PC A's `kind` *and* its local
file path, so the fixture proves two producer PCs reporting identical local
names cannot collide in the canonical file.

Usage:

    uv run python scripts/hzdr-openpmd-fixture.py --out-dir build/openpmd-fixture
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

os.environ.setdefault("DW_API_DAMNIT_PATH", str(Path.cwd()))

import h5py
import numpy as np

from damnit_api.metadata.hzdr_nexus import (
    discover_labfrog_data_products,
    read_labfrog_nexus_shots,
    reconcile_canonical_shots,
    single_writer_lock,
    write_nexus_bridge,
    write_sources_catalog,
)

EXPERIMENT_ID = "HZDR-OPENPMD-SYNTH-01"
SOURCE_KEY = "hzdr-openpmd-synth-01"
CAMPAIGN = "OPENPMD-SYNTH"
SHOT_DATE = "2026-08-31"

# One local path reported by both Watchdog PCs. Distinguishing their products
# has to come from the event identity, not from this string.
SHARED_WATCHDOG_FILE = "Z:/daq/beam_profile.h5"


def write_labfrog_export(path: Path) -> None:
    """Write the three-row LabFrog shot table the campaign is reconciled against."""
    string_dtype = h5py.string_dtype(encoding="utf-8")
    with h5py.File(path, "w") as handle:
        entry = handle.create_group("entry")
        entry.attrs["NX_class"] = "NXentry"
        shots = entry.create_group("shots")
        shots.attrs["NX_class"] = "NXcollection"
        shots.create_dataset("shot_index", data=[0, 1, 2])
        shots.create_dataset(
            "record_id",
            data=np.asarray(["lf-0001", "lf-0002", "lf-0003"], dtype=string_dtype),
        )
        shots.create_dataset("shot_number", data=[101, 102, 103])
        shots.create_dataset(
            "shot_date", data=np.asarray([SHOT_DATE] * 3, dtype=string_dtype)
        )
        shots.create_dataset(
            "date_time",
            data=np.asarray(
                [
                    f"{SHOT_DATE}T12:00:00Z",
                    f"{SHOT_DATE}T12:01:00Z",
                    f"{SHOT_DATE}T12:02:00Z",
                ],
                dtype=string_dtype,
            ),
        )
        shots.create_dataset(
            "campaign", data=np.asarray([CAMPAIGN] * 3, dtype=string_dtype)
        )
        derived = entry.create_group("derived")
        derived.attrs["NX_class"] = "NXcollection"
        charge = derived.create_dataset("ict_charge", data=[1.24, 1.41, 1.08])
        charge.attrs["units"] = "nC"


def _event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "schema_version": "hzdr-event-v1",
        "experiment_id": EXPERIMENT_ID,
        "transport": "kafka",
        "payload_ref": {},
        "metadata": {},
    }
    event.update(overrides)
    return event


def _trigger(*, shot_number: int, offset: int, timestamp: str) -> dict[str, Any]:
    return _event(
        event_id=f"trigger-{shot_number}",
        shot_id=f"shot-{shot_number:06d}",
        shot_number=shot_number,
        source="shotcounter",
        kind="draco.trigger",
        timestamp=timestamp,
        payload_ref={"topic": "hzdr.trigger", "partition": 0, "offset": offset},
        metadata={"trigger": {"role": "primary"}},
    )


def _watchdog(
    *,
    event_id: str,
    shot_number: int,
    offset: int,
    timestamp: str,
    instance_id: str,
    values: list[float],
) -> dict[str, Any]:
    """A PLANET Watchdog product event from one named producer PC.

    `metadata.producer.instance_id` is the additive, non-envelope-breaking
    spelling the root plan prefers for multi-PC identity. It is free-form
    metadata today, so it lands in `/entry/source_events/metadata_json` rather
    than in a column of its own - which is exactly the gap the preflight
    reports.
    """
    return _event(
        event_id=event_id,
        shot_id=f"shot-{shot_number:06d}",
        shot_number=shot_number,
        source="planet-watchdog",
        kind="beam_profiler",
        timestamp=timestamp,
        payload_ref={
            "topic": "hzdr.watchdog",
            "partition": 0,
            "offset": offset,
            "path": SHARED_WATCHDOG_FILE,
            "dataset_path": "/image",
            "preview_kind": "image",
            "shape": [2, 2],
            "dtype": "float64",
        },
        values=values,
        metadata={
            "unit": "count",
            "producer": {"instance_id": instance_id, "host": instance_id},
            "parser": {"name": "beam-profiler", "version": "1.4.0"},
            "diagnostic": {"detector_signal_mean": round(sum(values) / len(values), 4)},
        },
    )


def build_events() -> list[dict[str, Any]]:
    """The synthetic multi-source event stream, in publication order."""
    return [
        _trigger(shot_number=101, offset=501, timestamp=f"{SHOT_DATE}T12:00:00Z"),
        _watchdog(
            event_id="watchdog-pc-a-101",
            shot_number=101,
            offset=901,
            timestamp=f"{SHOT_DATE}T12:00:00.4Z",
            instance_id="watchdog-pc-a",
            values=[12.5, 13.0, 12.8, 13.2],
        ),
        _event(
            event_id="laserdata-101",
            shot_id="shot-000101",
            shot_number=101,
            source="LaserData",
            kind="laser_shot",
            timestamp=f"{SHOT_DATE}T12:00:00.2Z",
            transport="asapo",
            payload_ref={
                "uri": "asapo://hzdr/laserdata/101",
                "message_id": 101,
                "dataset_path": "/data",
            },
            values=[[1.0, 2.0], [3.0, 4.0]],
            metadata={
                "unit": "count",
                "producer": {"instance_id": "asapo-laserdata-01"},
                "laser": {
                    "system": "DRACO",
                    "pulse_energy": 8.2,
                    "pulse_duration": 30.0,
                    "wavelength": 800.0,
                    "repetition_rate": 10.0,
                    "polarization": "p",
                },
            },
        ),
        _trigger(shot_number=102, offset=502, timestamp=f"{SHOT_DATE}T12:01:00Z"),
        _watchdog(
            event_id="watchdog-pc-b-102",
            shot_number=102,
            offset=902,
            timestamp=f"{SHOT_DATE}T12:01:00.4Z",
            instance_id="watchdog-pc-b",
            values=[9.1, 9.4, 9.2, 9.6],
        ),
        _trigger(shot_number=103, offset=503, timestamp=f"{SHOT_DATE}T12:02:00Z"),
        # Early/orphan: a shot number no LabFrog row carries, three days before
        # the campaign. Reconciliation must leave it unassigned and visible.
        _watchdog(
            event_id="watchdog-pc-a-orphan",
            shot_number=999,
            offset=777,
            timestamp="2026-08-28T09:15:00Z",
            instance_id="watchdog-pc-a",
            values=[0.4, 0.5],
        ),
    ]


def projection_plan(*, source_ref: str) -> dict[str, Any]:
    """A reviewed plan over the canonical paths this fixture actually writes.

    Every `source_path` below was read back off the built file, not guessed.
    The one deliberate gap is
    `/entry/source_events/producer_instance_id`: the multi-PC identity lives in
    `metadata_json` today, so the preflight is expected to report it missing.
    """
    return {
        "schema_version": "1.0",
        "openpmd_standard": "1.1.0",
        "title": "DAMNIT synthetic multi-source campaign projection",
        "description": (
            "Design-time plan resolved against the synthetic three-shot canonical "
            "fixture. No openPMD file is written by the preflight."
        ),
        "source": {
            "kind": "damnit_campaign_nexus",
            "source_ref": source_ref,
            "entry_path": "/entry",
        },
        # Stated explicitly rather than defaulted: these are reviewed numbers
        # about this facility's data. 16 MiB clears every measured Watchdog
        # product; ASAPO payloads reach GB and degrade to a reference.
        "payload_policy": {
            "max_resolve_bytes": 16 * 1024 * 1024,
            "on_oversize": "reference_only",
            "on_pending": "reference_only",
            "require_checksum": False,
        },
        "iteration": {
            "index_path": "/entry/shots/shot_index",
            # DAMNIT's stable per-shot identity is `shot_key`; there is no
            # `shot_id` column in the canonical table.
            "shot_id_path": "/entry/shots/shot_key",
            "shot_number_path": "/entry/shots/shot_number",
            "trigger_time_path": "/entry/shots/fired_at",
            "match_quality_path": "/entry/shots/match_quality",
        },
        "rules": [
            {
                "source": "shotcounter",
                "source_path": "/entry/shots/shot_number",
                "role": "iteration_attribute",
                "target_name": "hzdr.shot_number",
                "materialization": "inline",
                "required": True,
                "description": "TANGO-authoritative shot number after reconciliation.",
            },
            {
                "source": "damnit",
                "source_path": "/entry/shots/match_quality",
                "role": "iteration_attribute",
                "target_name": "hzdr.match_quality",
                "materialization": "inline",
                "required": True,
                "description": "Reconciliation evidence carried onto the iteration.",
            },
            {
                "source": "labfrog",
                "source_path": "/entry/derived/ict_charge",
                "role": "scalar_mesh",
                "target_name": "ict_charge",
                "component": "value",
                "materialization": "inline",
                "required": True,
                "description": "Per-shot LabFrog product preserved by the bridge.",
            },
            {
                "source": "asapo",
                "source_path": "/entry/instrument/laser/shot_series/pulse_energy",
                "role": "scalar_mesh",
                "target_name": "laser_pulse_energy",
                "component": "value",
                "materialization": "inline",
                "description": (
                    "LaserData series; NaN on shot 103, which has no ASAPO event."
                ),
            },
            {
                "source": "planet-watchdog",
                "source_path": "/entry/instrument/detector_signal_mean/data",
                "role": "scalar_mesh",
                "target_name": "detector_signal_mean",
                "component": "value",
                "materialization": "inline",
                "event_id_path": "/entry/source_events/event_id",
                "producer_instance_path": "/entry/source_events/producer_instance_id",
                "description": (
                    "Watchdog scalar. producer_instance_path is the open contract "
                    "item: today the PC identity is inside metadata_json."
                ),
            },
            {
                "source": "planet-watchdog",
                "source_path": "/entry/data_products/dataset_path",
                "role": "reference",
                "target_name": "watchdog_product",
                "materialization": "reference_only",
                "event_id_path": "/entry/source_events/event_id",
                "description": "File/product reference kept without copying bulk data.",
            },
            {
                "source": "asapo",
                "source_path": "/entry/source_events/payload_ref_json",
                "role": "mesh",
                "target_name": "laser_near_field",
                "component": "value",
                "materialization": "resolve_payload",
                "payload_selector": "/data",
                "event_id_path": "/entry/source_events/event_id",
                "description": (
                    "Declares intent; payload_policy decides per payload. "
                    "Watchdog files are pulled after the Kafka message, so "
                    "'not local yet' degrades to a reference rather than "
                    "failing the run."
                ),
            },
        ],
    }


def build_fixture(out_dir: Path) -> dict[str, Path]:
    """Build the canonical fixture and its projection plan under `out_dir`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    labfrog_nexus = out_dir / "labfrog-openpmd-synth.nxs"
    output_nexus = out_dir / "canonical-openpmd-synth.nxs"
    sources_file = out_dir / "hzdr_sources.json"
    plan_file = out_dir / "openpmd-projection-plan.json"

    write_labfrog_export(labfrog_nexus)
    labfrog_shots = read_labfrog_nexus_shots(labfrog_nexus)
    shots, events = reconcile_canonical_shots(
        build_events(),
        experiment_id=EXPERIMENT_ID,
        source_key=SOURCE_KEY,
        labfrog_shots=labfrog_shots,
    )
    for product in discover_labfrog_data_products(labfrog_nexus, shots):
        shots[product["metadata"]["shot_index"]]["data_products"].append(product)

    with single_writer_lock(output_nexus):
        write_nexus_bridge(
            output_path=output_nexus,
            source_nexus=labfrog_nexus,
            experiment_id=EXPERIMENT_ID,
            shots=shots,
            events=events,
        )
    write_sources_catalog(
        sources_file=sources_file,
        source_key=SOURCE_KEY,
        experiment_id=EXPERIMENT_ID,
        nexus_path=output_nexus,
        shots=shots,
    )
    plan_file.write_text(
        json.dumps(
            projection_plan(
                source_ref=f"{EXPERIMENT_ID} synthetic multi-source fixture"
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "labfrog_nexus": labfrog_nexus,
        "canonical_nexus": output_nexus,
        "sources_file": sources_file,
        "plan_file": plan_file,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Directory to write the fixture, catalog, and projection plan into.",
    )
    args = parser.parse_args()
    paths = build_fixture(args.out_dir)
    for name, path in paths.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
