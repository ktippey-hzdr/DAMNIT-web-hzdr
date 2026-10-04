"""HDF5 external links from the campaign file to bulk files events name.

See hzdr/docs/plans/external-links-plan.md.
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import pytest

from damnit_api.metadata.hzdr_nexus import (
    HZDR_BRIDGE_PROFILE_VERSION,
    reconcile_canonical_shots,
    write_nexus_bridge,
)
from damnit_api.metadata.hzdr_paths import parse_path_map

SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "hzdr-hdf5-builder.py"
SPEC = importlib.util.spec_from_file_location("hzdr_hdf5_builder", SCRIPT_PATH)
assert SPEC is not None
hzdr_hdf5_builder = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["hzdr_hdf5_builder"] = hzdr_hdf5_builder
SPEC.loader.exec_module(hzdr_hdf5_builder)

LINKS = "entry/data_product_links"


def _event(shot: int, payload_ref: dict[str, Any]) -> dict[str, Any]:
    return {
        "experiment_id": "HELPMI",
        "shot_id": f"shot-{shot:06d}",
        "shot_number": shot,
        "source": "LaserData",
        "kind": "camera_raw",
        "timestamp": f"2026-06-10T12:0{shot}:00Z",
        "transport": "asapo",
        "payload_ref": payload_ref,
        "metadata": {},
    }


def _bulk_file(path: Path, dataset: str = "/entry/data/image") -> np.ndarray:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = np.arange(12, dtype=np.int32).reshape(3, 4)
    with h5py.File(path, "w") as handle:
        handle.create_dataset(dataset, data=image)
    return image


def _build(
    output: Path, events: list[dict[str, Any]], path_map: str = ""
) -> list[dict[str, Any]]:
    shots, normalized = reconcile_canonical_shots(
        events, experiment_id="HELPMI", source_key="hzdr", labfrog_shots=[]
    )
    return write_nexus_bridge(
        output_path=output,
        experiment_id="HELPMI",
        shots=shots,
        events=normalized,
        path_rules=parse_path_map(path_map),
    )


def _rows(output: Path) -> list[dict[str, Any]]:
    with h5py.File(output, "r") as handle:
        group = handle["entry/data_products"]
        assert isinstance(group, h5py.Group)
        ids = group["product_id"].asstr()[...]  # pyright: ignore[reportAttributeAccessIssue]
        index = group["product_index"][...]  # pyright: ignore[reportIndexIssue]
        meta = group["metadata_json"].asstr()[...]  # pyright: ignore[reportAttributeAccessIssue]
        keys = group["shot_key"].asstr()[...]  # pyright: ignore[reportAttributeAccessIssue]
        paths = group["path"].asstr()[...]  # pyright: ignore[reportAttributeAccessIssue]
    return [
        {
            "product_id": str(pid),
            "product_index": int(idx),
            "metadata": json.loads(m),
            "shot_key": str(key),
            "path": str(path),
        }
        for pid, idx, m, key, path in zip(ids, index, meta, keys, paths, strict=True)
    ]


def _reference_row(output: Path) -> dict[str, Any]:
    rows = [r for r in _rows(output) if r["product_id"].endswith(":reference")]
    assert len(rows) == 1
    return rows[0]


def test_hdf5_target_gets_a_link_that_resolves_to_the_dataset(tmp_path: Path):
    bulk = tmp_path / "bulk" / "shot1.h5"
    image = _bulk_file(bulk)
    output = tmp_path / "nexus" / "HELPMI.nxs"

    _build(
        output,
        [_event(1, {"hdf5_path": str(bulk), "dataset_path": "/entry/data/image"})],
    )

    row = _reference_row(output)
    link_name = f"/{LINKS}/{row['product_index']}"
    assert row["metadata"]["link"]["status"] == "linked"
    assert row["metadata"]["link"]["name"] == link_name
    with h5py.File(output, "r") as handle:
        link = handle.get(link_name, getlink=True)
        assert isinstance(link, h5py.ExternalLink)
        # Relative to the campaign file, so any mount of the share resolves it.
        assert not Path(link.filename).is_absolute()
        assert link.filename == "../bulk/shot1.h5"
        assert link.path == "/entry/data/image"
        np.testing.assert_array_equal(handle[link_name][...], image)  # pyright: ignore[reportIndexIssue]
        # The row still carries the recorded path and the joinable shot_key.
        assert row["path"] == str(bulk)
        assert row["shot_key"]


def test_link_without_dataset_path_targets_the_file_root(tmp_path: Path):
    bulk = tmp_path / "bulk" / "shot1.nxs"
    _bulk_file(bulk)
    output = tmp_path / "HELPMI.nxs"

    _build(output, [_event(1, {"path": str(bulk)})])

    row = _reference_row(output)
    with h5py.File(output, "r") as handle:
        link = handle.get(f"/{LINKS}/{row['product_index']}", getlink=True)
        assert isinstance(link, h5py.ExternalLink)
        assert link.path == "/"
        assert "entry" in handle[f"/{LINKS}/{row['product_index']}"]  # pyright: ignore[reportOperatorIssue]


def test_non_hdf5_target_keeps_its_row_but_gets_no_link(tmp_path: Path):
    tiff = tmp_path / "bulk" / "shot1.tif"
    tiff.parent.mkdir()
    tiff.write_bytes(b"II*\x00")
    output = tmp_path / "HELPMI.nxs"

    _build(output, [_event(1, {"path": str(tiff)})])

    row = _reference_row(output)
    assert row["path"] == str(tiff)
    assert row["metadata"]["link"] == {"status": "not_hdf5"}
    with h5py.File(output, "r") as handle:
        assert len(handle[LINKS]) == 0  # pyright: ignore[reportArgumentType]


def test_url_target_is_not_linked(tmp_path: Path):
    output = tmp_path / "HELPMI.nxs"
    _build(output, [_event(1, {"uri": "s3://bucket/shot1.h5"})])
    assert _reference_row(output)["metadata"]["link"] == {"status": "not_local_path"}


def test_missing_target_does_not_fail_the_build(tmp_path: Path):
    output = tmp_path / "HELPMI.nxs"
    missing = tmp_path / "later" / "shot1.h5"

    _build(output, [_event(1, {"hdf5_path": str(missing), "dataset_path": "/d"})])

    row = _reference_row(output)
    assert row["metadata"]["link"]["status"] == "missing_target"
    with h5py.File(output, "r") as handle:
        assert str(row["product_index"]) not in handle[LINKS]  # pyright: ignore[reportOperatorIssue]

    # The file appears; the next build links it.
    _bulk_file(missing, "/d")
    _build(output, [_event(1, {"hdf5_path": str(missing), "dataset_path": "/d"})])
    assert _reference_row(output)["metadata"]["link"]["status"] == "linked"


def test_missing_dataset_and_corrupt_file_are_recorded_not_raised(tmp_path: Path):
    bulk = tmp_path / "bulk" / "shot1.h5"
    _bulk_file(bulk, "/entry/data/image")
    corrupt = tmp_path / "bulk" / "shot2.h5"
    corrupt.write_bytes(b"not an hdf5 file")
    output = tmp_path / "HELPMI.nxs"

    _build(
        output,
        [
            _event(1, {"hdf5_path": str(bulk), "dataset_path": "/no/such"}),
            _event(2, {"hdf5_path": str(corrupt)}),
        ],
    )

    statuses = sorted(
        row["metadata"]["link"]["status"]
        for row in _rows(output)
        if row["product_id"].endswith(":reference")
    )
    assert statuses == ["missing_dataset", "unreadable"]
    with h5py.File(output, "r") as handle:
        assert len(handle[LINKS]) == 0  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    "recorded",
    [
        "Z:/bigdata/HPLexp/raw/shot1.h5",
        "Z:\\bigdata\\HPLexp\\raw\\shot1.h5",
        "/bigdata/HPLexp/raw/shot1.h5",
    ],
)
def test_recorded_share_paths_resolve_through_the_path_map(
    tmp_path: Path, recorded: str
):
    mount = tmp_path / "mnt" / "bigdata"
    image = _bulk_file(mount / "HPLexp" / "raw" / "shot1.h5", "/img")
    output = mount / "HPLexp" / "nexus" / "HELPMI" / "HELPMI.nxs"

    _build(
        output,
        [_event(1, {"hdf5_path": recorded, "dataset_path": "/img"})],
        path_map=f"/bigdata={mount},Z:/bigdata={mount}",
    )

    row = _reference_row(output)
    assert row["path"] == recorded  # the stored path is never rewritten
    assert row["metadata"]["link"]["target_file"] == "../../raw/shot1.h5"
    with h5py.File(output, "r") as handle:
        np.testing.assert_array_equal(
            handle[f"/{LINKS}/{row['product_index']}"][...],  # pyright: ignore[reportIndexIssue]
            image,
        )


def test_inline_values_and_campaign_file_rows_get_no_link(tmp_path: Path):
    output = tmp_path / "HELPMI.nxs"
    event = _event(1, {"message_id": 1})
    event["values"] = [1.0, 2.0]
    _build(output, [event])

    rows = _rows(output)
    assert rows
    assert all("link" not in row["metadata"] for row in rows)


def test_link_group_is_written_before_the_atomic_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    bulk = tmp_path / "bulk" / "shot1.h5"
    _bulk_file(bulk, "/d")
    output = tmp_path / "nexus" / "HELPMI.nxs"
    seen: dict[str, Any] = {}
    original = Path.replace

    def spy(self: Path, target: Any) -> Any:
        if self.name.endswith(".tmp.nxs"):
            seen["temp_dir"] = self.parent
            with h5py.File(self, "r") as handle:
                seen["links"] = list(handle[LINKS])  # pyright: ignore[reportArgumentType]
                seen["resolves"] = handle[f"{LINKS}/{seen['links'][0]}"].shape  # pyright: ignore[reportAttributeAccessIssue]
            seen["output_existed"] = Path(target).exists()
        return original(self, target)

    monkeypatch.setattr(Path, "replace", spy)
    _build(output, [_event(1, {"hdf5_path": str(bulk), "dataset_path": "/d"})])

    assert seen["temp_dir"] == output.parent
    assert len(seen["links"]) == 1
    assert seen["resolves"] == (3, 4)
    assert seen["output_existed"] is False


def test_data_products_stays_a_flat_table_and_profile_is_unchanged(tmp_path: Path):
    bulk = tmp_path / "bulk" / "shot1.h5"
    _bulk_file(bulk, "/d")
    output = tmp_path / "HELPMI.nxs"
    _build(output, [_event(1, {"hdf5_path": str(bulk), "dataset_path": "/d"})])

    assert HZDR_BRIDGE_PROFILE_VERSION == "hzdr-canonical-shot-v5"
    with h5py.File(output, "r") as handle:
        assert handle.attrs["damnit_bridge_profile"] == "hzdr-canonical-shot-v5"
        table = handle["entry/data_products"]
        assert isinstance(table, h5py.Group)
        lengths = set()
        for name in table:
            item = table.get(name, getlink=True)
            assert isinstance(item, h5py.HardLink)
            lengths.add(table[name].shape[0])  # pyright: ignore[reportAttributeAccessIssue]
        assert len(lengths) == 1
        links = handle[LINKS]
        assert links.attrs["NX_class"] == "NXcollection"
        assert links.attrs["damnit_source"] == "data_products"


def test_builder_cli_threads_the_path_map(tmp_path: Path):
    mount = tmp_path / "mnt"
    image = _bulk_file(mount / "raw" / "shot1.h5", "/img")
    events_jsonl = tmp_path / "events.jsonl"
    events_jsonl.write_text(
        json.dumps(
            _event(1, {"hdf5_path": "Z:/bigdata/raw/shot1.h5", "dataset_path": "/img"})
        )
        + "\n",
        encoding="utf-8",
    )
    output = mount / "nexus" / "HELPMI.nxs"
    args = argparse.Namespace(
        events_jsonl=[events_jsonl],
        event_json=[],
        watchdog_jsonl=[],
        trigger_jsonl=[],
        labfrog_nexus=None,
        labfrog_sqlite=None,
        mongo_uri=None,
        mongo_database=None,
        mongo_collection=None,
        mongo_query_json="",
        experiment_id="HELPMI",
        source_key="hzdr",
        output_nexus=output,
        sources_file=tmp_path / "hzdr_sources.json",
        match_tolerance_s=120.0,
        campaign_timezone="UTC",
        path_map=f"Z:/bigdata={mount}",
    )

    hzdr_hdf5_builder.build(args)

    row = _reference_row(output)
    assert row["metadata"]["link"]["status"] == "linked"
    with h5py.File(output, "r") as handle:
        np.testing.assert_array_equal(
            handle[f"/{LINKS}/{row['product_index']}"][...],  # pyright: ignore[reportIndexIssue]
            image,
        )
