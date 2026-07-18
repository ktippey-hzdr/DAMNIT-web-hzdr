"""Characterization tests for the openPMD simulation-link namespace.

Pins the contract of hzdr/docs/openpmd-linking.md: `metadata.simulation` is a
reserved, registry-exempt namespace that rides the ordinary hzdr-event-v1
metadata path — the normalizer preserves it byte-for-byte (object and array
forms) and the registry linter stays silent on it. No comparison tooling is
implied; these tests exist so the link references survive the pipeline
losslessly.
"""

from damnit_api.metadata.hzdr_event import (
    METADATA_KEY_REGISTRY,
    HZDREventV1,
    lint_metadata_keys,
)
from damnit_api.metadata.hzdr_nexus import _normalize_event

SIMULATION_LINK = {
    "series_uri": "file:///bigdata/picongpu/lwfa-042/simData_%T.bp",
    "iteration": 1200,
    "code": "PIConGPU",
    "code_version": "0.8.0",
    "checksum": "sha256:9f2c0000",
    "notes": "best-match density profile",
}


def event_with_simulation(simulation):
    return {
        "experiment_id": "HELPMI",
        "shot_id": "shot-000017",
        "source": "LaserData",
        "kind": "camera_raw",
        "timestamp": "2026-06-10T12:00:00Z",
        "transport": "asapo",
        "payload_ref": {"message_id": 17},
        "metadata": {"simulation": simulation},
    }


def test_simulation_link_object_round_trips_the_normalizer():
    normalized = _normalize_event(event_with_simulation(SIMULATION_LINK))

    assert normalized["metadata"]["simulation"] == SIMULATION_LINK


def test_simulation_link_array_form_round_trips_the_normalizer():
    links = [
        {"path": "/bigdata/picongpu/lwfa-042", "iteration": [1100, 1300]},
        {"series_uri": "https://scicat.example/PID", "scicat_pid": "20.500/x"},
    ]

    normalized = _normalize_event(event_with_simulation(links))

    assert normalized["metadata"]["simulation"] == links


def test_simulation_link_validates_as_hzdr_event_v1():
    HZDREventV1.model_validate(
        {**event_with_simulation(SIMULATION_LINK), "event_id": "evt-1"}
    )


def test_linter_is_silent_on_the_simulation_namespace():
    assert lint_metadata_keys({"simulation": SIMULATION_LINK}) == []


def test_simulation_namespace_has_no_registry_rows():
    # Non-numeric namespace: nothing to unit-stamp, by design
    # (openpmd-linking.md section 1).
    assert not any(key.startswith("simulation.") for key in METADATA_KEY_REGISTRY)
