"""Contract checks for the explicitly synthetic semantic golden input."""

from __future__ import annotations

import json
from pathlib import Path

from damnit_api.metadata.hzdr_event import HZDREventV1, lint_metadata_keys

FIXTURE = Path(__file__).parent / "fixtures" / "semantic-golden-domain.synthetic.json"


def test_semantic_golden_fixture_is_a_canonical_lint_clean_event():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    event = HZDREventV1.model_validate(payload)

    assert event.source == "DAMNIT-Synthetic-Acceptance"
    assert event.payload_ref.uri == "fixture://semantic-golden-domain.synthetic.json"
    assert lint_metadata_keys(event.metadata) == []


def test_semantic_golden_fixture_covers_non_null_domain_values():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    metadata = payload["metadata"]

    assert set(metadata) == {"laser", "vacuum", "diagnostic", "simulation"}
    assert all(
        value is not None for block in metadata.values() for value in block.values()
    )
