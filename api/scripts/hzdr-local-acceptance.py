"""Prove the local HZDR vertical slice end to end, from a clean state.

emulator events -> HZDREventV1 -> JSONL staging -> catalog rebuild
-> review API -> Confirm Matches -> export hook (NeXus/HDF5)

This script needs nothing outside this repository - no sibling checkout, no
Docker, no Mongo/Kafka/ASAPO. It writes a couple of minimal HZDREventV1-shaped
JSONL events (the staging step) plus a tiny synthetic LabFrog shot table (the
same shape api/tests/test_hzdr_integration.py uses, not the real curated
data - see hzdr/docs/status/handoff.md for why), runs them through the same
reconcile/build functions hzdr-hdf5-builder.py uses, then boots the real
FastAPI app in-process and exercises the actual HTTP review/confirm/dismiss
routes against the rebuilt catalog.

Usage:

    cd api
    uv run python scripts/hzdr-local-acceptance.py
    uv run python scripts/hzdr-local-acceptance.py --keep  # inspect output after

Exits non-zero (and prints which step failed) if any step does not hold.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "api" / "src"))

EXPERIMENT_ID = "hzdr-local-acceptance"
SOURCE_KEY = "hzdr-local-acceptance"
SEMANTIC_FIXTURE = (
    REPO_ROOT / "api" / "tests" / "fixtures" / "semantic-golden-domain.synthetic.json"
)
SEMANTIC_EVENT_ID = "acc-evt-semantic-2"


def write_minimal_labfrog_sqlite(path: Path) -> None:
    """Write a tiny, synthetic shots table - same schema as a real LabFrog
    curated export (see GitLab/labfrog-sqlite-tools-repo's MANIFEST.txt) but
    no real shot data, names, or comments. One deliberate same-day/
    same-shot_number collision (shot_number=1 twice on 2026-01-01, both
    active and equidistant from the shot-1 event) so the matcher has a genuine
    ambiguous case to put in front of Confirm Matches, plus one clean
    shot_number=2 that should match without review.

    Both shot-1 rows must be `active`: an archived+active pair is a version
    supersession the matcher correctly collapses (see
    hzdr_nexus._mark_superseded_labfrog_rows), which is *not* ambiguous.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("""
            CREATE TABLE shots (
                mongo_id TEXT PRIMARY KEY,
                shot_number INTEGER,
                version INTEGER,
                date_time TEXT,
                campaign TEXT,
                status TEXT,
                target TEXT,
                target_name TEXT,
                target_material TEXT,
                target_thickness_value REAL,
                target_thickness_unit TEXT,
                target_notes TEXT,
                target_source TEXT,
                target_series_sample TEXT
            )
        """)
        connection.executemany(
            "INSERT INTO shots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                # Two distinct *active* records, same day, same shot_number: a
                # shot-numbering collision the matcher cannot auto-resolve. Both
                # survive supersede filtering (both active) and sit equidistant
                # (+/-2 s) from the LaserData shot-1 event at 09:00:02, so time
                # disambiguation ties -> a genuine ambiguous case for Confirm
                # Matches.
                (
                    "acc-shot-1a",
                    1,
                    0,
                    "2026-01-01T09:00:00",
                    EXPERIMENT_ID,
                    "active",
                    "OTHER: synthetic Kapton witness",
                    "Synthetic Kapton witness",
                    "Kapton",
                    0.5,
                    "um",
                    "Synthetic acceptance fixture; not facility data",
                    "operator",
                    "SYN-01",
                ),
                (
                    "acc-shot-1b",
                    1,
                    0,
                    "2026-01-01T09:00:04",
                    EXPERIMENT_ID,
                    "active",
                    "OTHER: synthetic Kapton witness",
                    "Synthetic Kapton witness",
                    "Kapton",
                    0.5,
                    "um",
                    "Synthetic acceptance fixture; not facility data",
                    "operator",
                    "SYN-01",
                ),
                # One unambiguous shot.
                (
                    "acc-shot-2-v0",
                    2,
                    0,
                    "2026-01-01T09:10:00",
                    EXPERIMENT_ID,
                    "active",
                    "OTHER: synthetic Kapton witness",
                    "Synthetic Kapton witness",
                    "Kapton",
                    0.5,
                    "um",
                    "Synthetic acceptance fixture; not facility data",
                    "operator",
                    "SYN-01",
                ),
            ],
        )
        connection.commit()


def write_staged_events(events_dir: Path) -> list[Path]:
    """Write minimal HZDREventV1-shaped JSONL events - the staging step.

    Three producers, not just one, so the rebuilt catalog/review payload
    shows real producer diversity rather than only ever exercising
    LaserData's adapter-free path:

    - LaserData (shot_number=1): lands on the ambiguous LabFrog pair, by
      design - exercises "ambiguous".
    - LaserData (shot_number=99): matches nothing, by design - exercises
      "unmatched".
    - DAQ File Watchdog (shot_number=2, via normalize_watchdog_document): a
      raw watchdog-shaped document, adapted the same way a real watchdog
      consumer would adapt it before staging.
    - DRACO-Trigger (shot_number=2, via normalize_processed_trigger_message):
      a raw legacy trigger payload, same adapter the shotcounter/DRACO
      boundary uses. Both land on the same clean shot_number=2 match so that
      shot ends up multi-producer-matched, not just multi-event.
    """
    from damnit_api.metadata.hzdr_nexus import (
        normalize_processed_trigger_message,
        normalize_watchdog_document,
    )

    events_dir.mkdir(parents=True, exist_ok=True)

    def laser_event(
        *, shot_number: int, event_id: str, timestamp: str
    ) -> dict[str, Any]:
        return {
            "schema_version": "hzdr-event-v1",
            "event_id": event_id,
            "experiment_id": EXPERIMENT_ID,
            "shot_id": f"shot-{shot_number:06d}",
            "shot_number": shot_number,
            "source": "LaserData",
            "kind": "pulse_energy_j",
            "timestamp": timestamp,
            "transport": "asapo",
            "payload_ref": {"stream": "laser", "message_id": shot_number},
            "values": [12.4 + shot_number * 0.1],
            "metadata": {"unit": "J"},
        }

    laserdata_path = events_dir / "laserdata.jsonl"
    laser_events = [
        laser_event(
            shot_number=1, event_id="acc-evt-1", timestamp="2026-01-01T09:00:02Z"
        ),
        laser_event(
            shot_number=99, event_id="acc-evt-3", timestamp="2026-01-01T12:00:00Z"
        ),
    ]
    with laserdata_path.open("w", encoding="utf-8") as handle:
        for record in laser_events:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    watchdog_event = normalize_watchdog_document(
        {
            "shot_id": "shot-000002",
            "timestamp": "2026-01-01T09:10:03Z",
            "watch": {"watch_name": "acceptance check"},
            "event": {
                "filename": "shot-2.csv",
                "filepath": "Z:/acceptance/shot-2.csv",
            },
            "analysis": {"data": {"shot": 2, "energy": 8.1}},
            "_kafka": {"topic": "planet.watchdog.events", "offset": 1},
        },
        experiment_id=EXPERIMENT_ID,
    )
    watchdog_event["event_id"] = "acc-evt-watchdog-2"
    watchdog_path = events_dir / "watchdog.jsonl"
    watchdog_path.write_text(
        json.dumps(watchdog_event, sort_keys=True) + "\n", encoding="utf-8"
    )

    trigger_event = normalize_processed_trigger_message({
        "processed_message": {
            "Name": "Draco01",
            "Campaign": EXPERIMENT_ID,
            "nickname": "acceptance_trigger",
            "trigger_role": "pump",
            "threshold": 0.25,
            "adc_value": 0.81,
            "channel_trigger_count": 1,
            "run_id": 1,
            "timestamp": "2026-01-01T09:10:04Z",
            "sample_counter_10hz": 1,
            "shot_number": 2,
        },
        "_kafka": {
            "topic": "Draco01",
            "partition": 0,
            "offset": 1,
            "key": "processed_message",
        },
    })
    trigger_event["event_id"] = "acc-evt-trigger-2"
    trigger_path = events_dir / "trigger.jsonl"
    trigger_path.write_text(
        json.dumps(trigger_event, sort_keys=True) + "\n", encoding="utf-8"
    )

    semantic_event = json.loads(SEMANTIC_FIXTURE.read_text(encoding="utf-8"))
    semantic_path = events_dir / "semantic-domain.jsonl"
    semantic_path.write_text(
        json.dumps(semantic_event, sort_keys=True) + "\n", encoding="utf-8"
    )

    return [laserdata_path, watchdog_path, trigger_path, semantic_path]


def verify_semantic_nexus(path: Path) -> None:
    """Assert that every synthetic domain block survives into its NeXus home."""
    import h5py

    fixture = json.loads(SEMANTIC_FIXTURE.read_text(encoding="utf-8"))
    with h5py.File(path, "r") as handle:
        entry = handle["entry"]
        assert entry["definition"].asstr()[()] == "NXhzdr_target"

        sample = entry["sample"]
        assert sample.attrs["NX_class"] == "NXsample"
        assert sample["name"].asstr()[()] == "Synthetic Kapton witness"
        assert sample["material"].asstr()[()] == "Kapton"
        assert math.isclose(sample["thickness"][()], 500.0)
        assert sample["thickness"].attrs["units"] == "nm"
        assert sample.attrs["damnit_provenance"] == "manual"

        laser = entry["instrument/laser"]
        assert laser["name"].asstr()[()] == "DRACO synthetic"
        assert math.isclose(laser["frequency"][()], 10.0)
        assert laser["frequency"].attrs["units"] == "Hz"
        assert math.isclose(laser["pulse_energy"][()], 8.2)
        assert laser["pulse_energy"].attrs["units"] == "J"
        assert laser["beam/pulse_duration"].attrs["units"] == "fs"
        assert laser["beam/incident_wavelength"].attrs["units"] == "nm"

        shot_keys = entry["shots/shot_key"].asstr()[...].tolist()
        shot_index = shot_keys.index(f"{EXPERIMENT_ID}:20260101:000002")
        assert math.isclose(laser["shot_series/pulse_energy"][shot_index], 8.2)
        assert laser["shot_series/pulse_energy"].attrs["units"] == "J"

        environment = entry["sample/environment"]
        assert math.isclose(environment["chamber_pressure"][()], 2.4e-6)
        assert environment["chamber_pressure"].attrs["units"] == "mbar"
        assert math.isclose(environment["pre_shot_pressure"][()], 1.1e-6)
        assert environment["rga_dominant_species"].asstr()[()] == "H2O"

        xray = entry["instrument/xray_counts/data"]
        assert math.isclose(xray[shot_index], 1450.0)
        assert xray.attrs["units"] == "counts"
        assert math.isclose(
            entry["instrument/detector_signal_mean/data"][shot_index], 2.25
        )
        assert math.isclose(entry["instrument/alignment_score/data"][shot_index], 0.98)

        event_ids = entry["source_events/event_id"].asstr()[...].tolist()
        event_index = event_ids.index(SEMANTIC_EVENT_ID)
        metadata = json.loads(entry["source_events/metadata_json"].asstr()[event_index])
        assert metadata == fixture["metadata"]
        payload_ref = json.loads(
            entry["source_events/payload_ref_json"].asstr()[event_index]
        )
        assert payload_ref == fixture["payload_ref"]


def rebuild_catalog(
    *, events_dir: Path, labfrog_sqlite: Path, output_dir: Path
) -> tuple[Path, Path]:
    """Run the same reconcile/build steps hzdr-hdf5-builder.py runs."""
    from damnit_api.metadata.hzdr_nexus import (
        load_normalized_events,
        read_labfrog_sqlite_shots,
        reconcile_canonical_shots,
        write_nexus_bridge,
        write_sources_catalog,
    )

    events = load_normalized_events(sorted(events_dir.glob("*.jsonl")))
    labfrog_shots = read_labfrog_sqlite_shots(labfrog_sqlite)
    shots, normalized_events = reconcile_canonical_shots(
        events,
        experiment_id=EXPERIMENT_ID,
        source_key=SOURCE_KEY,
        labfrog_shots=labfrog_shots,
        campaign_timezone="UTC",
    )

    output_nexus = output_dir / f"{EXPERIMENT_ID}.nxs"
    sources_file = output_dir / "hzdr_sources.json"
    write_nexus_bridge(
        output_path=output_nexus,
        experiment_id=EXPERIMENT_ID,
        shots=shots,
        events=normalized_events,
    )
    write_sources_catalog(
        sources_file=sources_file,
        source_key=SOURCE_KEY,
        experiment_id=EXPERIMENT_ID,
        nexus_path=output_nexus,
        shots=shots,
        events=normalized_events,
    )
    return output_nexus, sources_file


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_event_replay_identity(events_dir: Path) -> list[str]:
    """Load the staged inputs twice and prove stable, unique event identity."""
    from damnit_api.metadata.hzdr_nexus import load_normalized_events

    paths = sorted(events_dir.glob("*.jsonl"))
    first = [event["event_id"] for event in load_normalized_events(paths)]
    second = [event["event_id"] for event in load_normalized_events(paths)]
    assert first == second
    assert len(first) == len(set(first)), "fixture event_id values must be unique"
    return first


def verify_local_scicat_replay(
    *, nexus_path: Path, sources_file: Path, event_ids: list[str]
) -> dict[str, Any]:
    """Prove catalog persistence and unchanged-artifact SciCat POST dedup.

    This deliberately mocks only the remote HTTP response. The DAMNIT request
    builder, source checksum, catalog stamping/readback, and replay skip are the
    production functions. It is local contract evidence, not a production-like
    SciCat deployment.
    """
    from unittest.mock import patch

    import httpx

    from damnit_api.metadata.hzdr_nexus import write_json_atomic
    from damnit_api.metadata.scicat import (
        read_previous_registration,
        register_campaign_nexus,
    )
    from damnit_api.shared.hzdr_settings import HZDRScicatSettings

    calls: list[dict[str, Any]] = []

    def local_post(url, json, timeout):
        calls.append({"url": url, "json": json, "timeout": timeout})
        checksum = json["files"][0]["checksum"]
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "pid": "synthetic-local/campaign",
                "version_hash": f"local-{checksum[:16]}",
            },
            request=request,
        )

    scicat_settings = HZDRScicatSettings(
        enabled=True,
        plugin_url="http://synthetic-scicat.invalid",
        frontend_url="http://synthetic-scicat.invalid",
        endpoint="push",
    )
    registration_args = {
        "settings": scicat_settings,
        "nexus_path": nexus_path,
        "experiment_id": EXPERIMENT_ID,
        "source_key": SOURCE_KEY,
        "scientific_metadata": {
            "fixtureClassification": "synthetic",
            "eventIds": event_ids,
        },
        "source_folder": str(nexus_path.parent),
    }
    with patch("httpx.post", side_effect=local_post):
        first = register_campaign_nexus(**registration_args)
        assert first is not None
        assert len(calls) == 1

        catalog = json.loads(sources_file.read_text(encoding="utf-8"))
        catalog["sources"][0]["metadata"].update(first)
        write_json_atomic(sources_file, catalog)
        previous = read_previous_registration(sources_file, SOURCE_KEY)
        assert previous == first

        second = register_campaign_nexus(**registration_args, previous=previous)
        assert second == first
        assert len(calls) == 1, "unchanged registration replay must not POST again"

    return {
        "mode": "mocked-local-contract",
        "pid": first["scicat_pid"],
        "versionHash": first["scicat_version_hash"],
        "sourceSha256": first["scicat_source_sha256"],
        "postCountAfterReplay": len(calls),
    }


def write_semantic_evidence_report(
    *,
    path: Path,
    nexus_path: Path,
    sources_file: Path,
    event_ids: list[str],
    scicat: dict[str, Any],
) -> None:
    report = {
        "schemaVersion": 1,
        "classification": "synthetic",
        "experimentId": EXPERIMENT_ID,
        "fixture": {
            "path": SEMANTIC_FIXTURE.name,
            "sha256": sha256_file(SEMANTIC_FIXTURE),
        },
        "normalizedEventIds": event_ids,
        "nexus": {
            "path": nexus_path.name,
            "sha256": sha256_file(nexus_path),
        },
        "catalog": {
            "path": sources_file.name,
            "sha256": sha256_file(sources_file),
        },
        "scicat": scicat,
    }
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


class Step:
    """Print a step header and re-raise with context on failure."""

    def __init__(self, label: str):
        self.label = label

    def __enter__(self) -> Step:
        print(f"\n== {self.label} ==")
        return self

    def __exit__(self, exc_type, exc, _tb) -> bool:
        if exc_type is None:
            print(f"OK: {self.label}")
            return False
        print(f"FAILED: {self.label}: {exc}", file=sys.stderr)
        return False


def run_acceptance(*, keep: bool, output_dir: Path | None = None) -> bool:
    if output_dir is None:
        work_dir = Path(tempfile.mkdtemp(prefix="hzdr-local-acceptance-"))
    else:
        work_dir = output_dir.resolve()
        if work_dir.exists() and any(work_dir.iterdir()):
            message = f"--output-dir must be absent or empty: {work_dir}"
            raise ValueError(message)
        work_dir.mkdir(parents=True, exist_ok=True)
        keep = True
    ok = True
    try:
        events_dir = work_dir / "events"
        labfrog_sqlite = work_dir / "labfrog.sqlite"
        output_dir = work_dir / "catalog"
        output_nexus = output_dir / f"{EXPERIMENT_ID}.nxs"
        sources_file = output_dir / "hzdr_sources.json"
        evidence_file = output_dir / "semantic-golden-evidence.json"

        # All settings env vars must be set before the first damnit_api
        # import anywhere in this process: Settings() is constructed once,
        # at first import, and later os.environ writes have no effect on the
        # already-built singleton (see api/tests/test_runtime_config.py's
        # test_flow_monitor_producer_options_overridable_via_env for the same
        # caveat documented against a fresh Settings() instead).
        os.environ["DW_API_DAMNIT_PATH"] = str(work_dir)
        os.environ["DW_API_METADATA__PROVIDER"] = "local"
        os.environ["DW_API_METADATA__SOURCES_FILE"] = str(sources_file)

        with Step("Write staged events (JSONL)"):
            staged_paths = write_staged_events(events_dir)
            assert all(path.exists() for path in staged_paths)

        with Step("Replay staged inputs with stable event identity"):
            event_ids = verify_event_replay_identity(events_dir)

        with Step("Write synthetic LabFrog source data"):
            write_minimal_labfrog_sqlite(labfrog_sqlite)

        with Step("Rebuild catalog from staged events"):
            built_nexus, built_sources_file = rebuild_catalog(
                events_dir=events_dir,
                labfrog_sqlite=labfrog_sqlite,
                output_dir=output_dir,
            )
            assert built_nexus == output_nexus
            assert built_sources_file == sources_file
            assert output_nexus.exists(), "export artifact (NeXus) was not written"
            assert sources_file.exists(), "derived catalog was not written"

        with Step("Verify synthetic semantic domains in NeXus"):
            verify_semantic_nexus(output_nexus)

        with Step("Register and replay against local SciCat contract"):
            scicat_evidence = verify_local_scicat_replay(
                nexus_path=output_nexus,
                sources_file=sources_file,
                event_ids=event_ids,
            )

        with Step("Boot API and verify review endpoint sees rebuilt state"):
            from fastapi.testclient import TestClient

            from damnit_api.main import create_app
            from damnit_api.shared.settings import settings

            # Confirm/dismiss require a real user (OAuthUserInfo.from_connection).
            # In local mode with auth=None, that dependency returns DEV_USER with
            # no session needed - the same fallback hzdr-dev.ps1/hzdr-package-
            # emulator.py rely on. Force it explicitly here so this script does
            # not depend on whether the developer's own api/.env happens to set
            # DW_API_AUTH__MODE (most local dev .env files do, for the LDAP
            # login page, which this offline acceptance check has no use for).
            settings.auth = None

            with TestClient(create_app()) as client:
                sources_response = client.get("/metadata/hzdr/sources")
                sources_response.raise_for_status()
                sources_payload = sources_response.json()
                assert any(source["key"] == SOURCE_KEY for source in sources_payload), (
                    f"{SOURCE_KEY} not visible via GET /metadata/hzdr/sources"
                )

                review_response = client.get(
                    f"/metadata/hzdr/sources/{SOURCE_KEY}/review"
                )
                review_response.raise_for_status()
                review_payload = review_response.json()
                summary = review_payload["match_summary"]
                review_events = review_payload["review_events"]
                print(f"  match_summary: {summary}")
                print(f"  review_events: {len(review_events)}")

                assert summary["matched"] >= 1, "expected at least one matched shot"
                ambiguous = [
                    event
                    for event in review_events
                    if event["match_status"] == "ambiguous"
                ]
                unmatched = [
                    event
                    for event in review_events
                    if event["match_status"] == "unmatched"
                ]
                assert ambiguous, (
                    "expected an ambiguous review event (the deliberate "
                    "duplicate shot_number=1 LabFrog row) - fixture or "
                    "matcher behavior changed"
                )
                assert unmatched, (
                    "expected an unmatched review event (shot_number=99) - "
                    "fixture or matcher behavior changed"
                )

            with Step("Confirm Matches: attach the ambiguous event over HTTP"):
                candidate_shot_key = ambiguous[0]["candidate_shot_keys"][0]
                confirm_response = client.post(
                    f"/metadata/hzdr/sources/{SOURCE_KEY}/review/"
                    f"{ambiguous[0]['event_id']}/confirm",
                    json={"shot_key": candidate_shot_key, "note": "acceptance run"},
                )
                confirm_response.raise_for_status()
                confirmed_source = confirm_response.json()
                assert confirmed_source["match_summary"]["confirmed"] == 1
                assert confirmed_source["match_summary"]["ambiguous"] == 0

            with Step("Dismiss the unmatched event over HTTP"):
                dismiss_response = client.post(
                    f"/metadata/hzdr/sources/{SOURCE_KEY}/review/"
                    f"{unmatched[0]['event_id']}/dismiss",
                    json={"note": "acceptance run"},
                )
                dismiss_response.raise_for_status()
                dismissed_source = dismiss_response.json()
                assert dismissed_source["match_summary"]["dismissed"] == 1
                assert dismissed_source["match_summary"]["unmatched"] == 0

            with Step("Re-fetch review: confirm/dismiss are reflected"):
                final_review = client.get(
                    f"/metadata/hzdr/sources/{SOURCE_KEY}/review"
                ).json()
                assert final_review["match_summary"]["confirmed"] == 1
                assert final_review["match_summary"]["dismissed"] == 1
                assert final_review["match_summary"]["ambiguous"] == 0
                assert final_review["match_summary"]["unmatched"] == 0

        with Step("Write synthetic golden evidence report"):
            write_semantic_evidence_report(
                path=evidence_file,
                nexus_path=output_nexus,
                sources_file=sources_file,
                event_ids=event_ids,
                scicat=scicat_evidence,
            )
            assert evidence_file.exists()

        print("\nAll local acceptance steps passed.")
        print(f"Catalog: {sources_file}")
        print(f"Export (NeXus/HDF5): {output_nexus}")
        print(f"Evidence: {evidence_file}")
        print(f"Generated at: {datetime.now(UTC).isoformat()}")
    except AssertionError as exc:
        print(f"\nAcceptance check failed: {exc}", file=sys.stderr)
        ok = False
    except Exception as exc:
        print(f"\nUnexpected error: {exc}", file=sys.stderr)
        ok = False
    finally:
        if keep:
            print(f"\n--keep set: left local state at {work_dir}")
        else:
            shutil.rmtree(work_dir, ignore_errors=True)
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Do not delete the generated temp directory; print its path instead.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Write the inspectable synthetic bundle to a new or empty directory.",
    )
    args = parser.parse_args()

    if not run_acceptance(keep=args.keep, output_dir=args.output_dir):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
