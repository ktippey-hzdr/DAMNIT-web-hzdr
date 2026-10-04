"""shot-aligner's mapping rows applied in h5py (campaign output phase 4b).

The reference fixture's container holds the real rows end to end
(test_hzdr_containers). These are the rules one at a time, on a small entry.
"""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np
import pytest

from damnit_api.metadata.hzdr_packs import mapping_rows
from damnit_api.metadata.hzdr_packs.mapping_rows import InstrumentMapping, apply_to

DETECTOR = "entry/Machine/Camera"


def _row(local_name: str, source: str, nexus_path: str, **extra) -> dict:
    return {
        "local_name": local_name,
        "source": source,
        "nexus_path": nexus_path,
        **extra,
    }


def _mapping(*rows: dict, definition: str | None = None, status="reviewed"):
    return InstrumentMapping(
        instrument="Cam",
        file_name="Cam.json",
        rows=list(rows),
        definition=definition,
        status=status,
        instrument_id="cam",
    )


@pytest.fixture
def handle(tmp_path: Path):
    with h5py.File(tmp_path / "shot.nxs", "w") as f:
        entry = f.create_group("entry")
        entry.attrs["NX_class"] = "NXentry"
        machine = entry.create_group("Machine")
        machine.attrs["NX_class"] = "NXinstrument"
        detector = machine.create_group("Camera")
        detector.attrs["NX_class"] = "NXdetector"
        image = detector.create_dataset("raw_data/image", data=np.ones((2, 3)))
        data = detector.create_group("data")
        data.attrs["NX_class"] = "NXdata"
        data.attrs["signal"] = "image"
        data["image"] = image
        width = detector.create_dataset("fit/width", data=2.0)
        width.attrs["units"] = "mm"
        detector.create_dataset("model", data="pco")
        entry.create_dataset("start_time", data="2025-12-01T15:59:04Z")
        yield f


def test_a_row_links_the_packs_dataset_under_a_second_name(handle):
    problems = apply_to(
        handle,
        DETECTOR,
        _mapping(_row("model", "model", "/entry/collection_Cam/camera_model")),
    )
    assert problems == []
    linked = handle["entry/collection_Cam/camera_model"]
    assert linked.id == handle[f"{DETECTOR}/model"].id  # one object, two names
    assert handle["entry/collection_Cam"].attrs["NX_class"] == "NXcollection"
    assert linked.attrs["mapped_from"] == "Cam"
    assert linked.attrs["mapping_status"] == "reviewed"
    assert linked.attrs["source_path"] == f"{DETECTOR}/model"
    assert linked.attrs["target"] == f"/{DETECTOR}/model"  # as makelink records


def test_a_transformed_row_writes_a_derived_dataset(handle):
    row = _row(
        "fwhm",
        "fit/width",
        "/entry/collection_Cam/fwhm",
        value_transform="gaussian_sigma_to_fwhm",
        convert_to_unit="um",
    )
    assert apply_to(handle, DETECTOR, _mapping(row)) == []
    derived = handle["entry/collection_Cam/fwhm"]
    assert derived.id != handle[f"{DETECTOR}/fit/width"].id
    assert derived[()] == pytest.approx(2.0 * 2.354_820_045_030_949_3 * 1000)
    assert derived.attrs["units"] == "um"
    assert derived.attrs["units_converted_from"] == "mm"
    assert derived.attrs["derived_from"] == f"{DETECTOR}/fit/width"
    assert "target" not in derived.attrs  # derived, not a link
    assert handle[f"{DETECTOR}/fit/width"][()] == pytest.approx(2.0)  # unchanged


def test_an_unknown_transform_is_reported_not_guessed(handle):
    row = _row("x", "fit/width", "/entry/collection_Cam/x", value_transform="nope")
    problems = apply_to(handle, DETECTOR, _mapping(row))
    assert "could not be derived" in problems[0]
    assert "entry/collection_Cam/x" not in handle


def test_a_source_the_acquisition_did_not_write_is_reported(handle):
    problems = apply_to(
        handle, DETECTOR, _mapping(_row("gain", "gain", "/entry/collection_Cam/gain"))
    )
    assert problems == [
        "Cam: mapping row 'gain' points at 'gain', which this acquisition did not write"
    ]


def test_the_second_claim_on_a_path_is_reported_and_the_first_kept(handle):
    first = _mapping(_row("model", "model", "/entry/shared/model"))
    apply_to(handle, DETECTOR, first)
    second = InstrumentMapping(
        instrument="Other",
        file_name="Other.json",
        rows=[_row("width", "fit/width", "/entry/shared/model")],
    )
    problems = apply_to(handle, DETECTOR, second)
    assert "which Cam already claimed" in problems[0]
    assert handle["entry/shared/model"].id == handle[f"{DETECTOR}/model"].id


def test_a_group_the_build_wrote_is_never_linked_over(handle):
    problems = apply_to(
        handle, DETECTOR, _mapping(_row("model", "model", f"/{DETECTOR}/raw_data"))
    )
    assert "not a dataset" in problems[0]
    assert isinstance(handle[f"{DETECTOR}/raw_data"], h5py.Group)


def test_a_dataset_no_mapping_wrote_is_left_alone(handle):
    problems = apply_to(
        handle, DETECTOR, _mapping(_row("t", "model", "/entry/start_time"))
    )
    assert "holds a dataset no mapping wrote" in problems[0]


def test_a_row_already_true_of_the_file_is_stamped_in_place(handle):
    """NDS asks for the detector's ``data`` to be its image: the NXdata says so."""
    row = _row("image", "raw_data/image", f"/{DETECTOR}/data")
    assert apply_to(handle, DETECTOR, _mapping(row)) == []
    assert isinstance(handle[f"{DETECTOR}/data"], h5py.Group)
    assert handle[f"{DETECTOR}/raw_data/image"].attrs["nds_local_name"] == "image"


def test_a_shot_level_row_naming_itself_is_nothing_to_do(handle):
    row = _row("start_time", "/entry/start_time", "/entry/start_time")
    assert apply_to(handle, DETECTOR, _mapping(row)) == []
    assert "mapped_from" not in handle["entry/start_time"].attrs


def test_a_row_placed_under_another_machine_is_reported_stale(handle):
    row = _row(
        "model", "model", "/entry/OldMachine/Camera/model", group_instance="Camera"
    )
    problems = apply_to(handle, DETECTOR, _mapping(row))
    assert "its family changed since" in problems[0]
    assert "entry/OldMachine" not in handle


def test_a_definition_claim_goes_to_a_subentry_that_plots_like_the_detector(handle):
    rows = (
        _row("image", "raw_data/image", "/entry/Camera/data/image"),
        _row("model", "model", "/entry/Camera/instrument/Camera/model"),
    )
    problems = apply_to(
        handle, DETECTOR, _mapping(*rows, definition="NXoptical_spectroscopy")
    )
    assert problems == []
    sub = handle["entry/Camera"]
    assert sub.attrs["NX_class"] == "NXsubentry"
    assert sub["definition"][()].decode() == "NXoptical_spectroscopy"
    assert sub["data"].attrs["NX_class"] == "NXdata"
    assert sub["data"].attrs["signal"] == "image"  # mirrored from the detector
    assert sub["instrument"].attrs["NX_class"] == "NXinstrument"
    assert sub["instrument/Camera"].attrs["NX_class"] == "NXdetector"
    assert handle[DETECTOR].attrs["nds_definition"] == "NXoptical_spectroscopy"
    assert handle[DETECTOR].attrs["nds_subentry"] == "/entry/Camera"
    assert "definition" not in handle["entry"]


def test_a_definition_without_a_subentry_is_claimed_only_for_a_sole_instrument(handle):
    row = _row("model", "model", "/entry/collection_Cam/model")
    claimed = _mapping(row, definition="NXfoo")
    problems = apply_to(handle, DETECTOR, claimed, sole_instrument=False)
    assert "Build a single-instrument file to claim it" in problems[0]
    assert "definition" not in handle["entry"]


def test_a_sole_instruments_definition_is_the_entrys(tmp_path):
    with h5py.File(tmp_path / "s.nxs", "w") as f:
        f.create_group("entry").attrs["NX_class"] = "NXentry"
        f.create_dataset(f"{DETECTOR}/model", data="pco")
        mapping = _mapping(
            _row("model", "model", "/entry/collection_Cam/model"), definition="NXfoo"
        )
        assert apply_to(f, DETECTOR, mapping, sole_instrument=True) == []
        assert f["entry/definition"][()].decode() == "NXfoo"


def test_a_proposed_mapping_is_written_and_says_so(handle):
    mapping = _mapping(
        _row("model", "model", "/entry/collection_Cam/model"), status="proposed"
    )
    apply_to(handle, DETECTOR, mapping)
    assert handle["entry/collection_Cam/model"].attrs["mapping_status"] == "proposed"


def test_rows_without_a_source_or_naming_an_attribute_are_not_written(handle):
    mapping = _mapping(
        _row("later", "", "/entry/collection_Cam/later"),
        _row("signal", "model", "/entry/collection_Cam/sig", nexus_attribute="signal"),
    )
    assert apply_to(handle, DETECTOR, mapping) == []
    assert "entry/collection_Cam" not in handle


def test_the_vendored_mappings_are_found_by_instrument_id():
    found = mapping_rows.by_id()
    assert "m1_spec_fib_cer" in found
    m1 = found["m1_spec_fib_cer"]
    assert m1.instrument == "M1_Spec_Fib_Cer"
    assert len(m1.sha256) == 64
    assert mapping_rows.for_instrument("no_such_instrument") is None


def test_a_mapping_change_rebuilds_only_the_shots_holding_it(monkeypatch):
    """The code digest leaves the mapping files out; each shot names its own."""
    from damnit_api.metadata import hzdr_containers as hc

    assert all(p.parent != mapping_rows.MAPPING_DIR for p in hc._source_files())
    plan = hc.ShotPlan.__new__(hc.ShotPlan)  # only what fingerprint() reads
    acquisitions = [
        hc.Acquisition(
            instrument_id="m1_spec_fib_cer",
            pack="camera_png_csv",
            instrument="M1_Spec_Fib_Cer",
            layout={},
            key="k",
        )
    ]
    for name, value in {
        "shot_key": "c:20251201:000001",
        "experiment_id": "c",
        "fired_at": "",
        "labfrog": {},
        "problems": [],
        "acquisitions": acquisitions,
    }.items():
        object.__setattr__(plan, name, value)
    before = hc.fingerprint(plan, lambda p: Path(p), {})
    original = mapping_rows.by_id()
    changed = {
        **original,
        "m1_spec_fib_cer": InstrumentMapping(**{
            **original["m1_spec_fib_cer"].__dict__,
            "sha256": "0" * 64,
        }),
        "probe135": InstrumentMapping(**{
            **original["probe135"].__dict__,
            "sha256": "1" * 64,
        }),
    }
    monkeypatch.setattr(mapping_rows, "by_id", lambda: changed)
    after_m1 = hc.fingerprint(plan, lambda p: Path(p), {})
    assert after_m1 != before
    changed["m1_spec_fib_cer"] = original["m1_spec_fib_cer"]
    assert hc.fingerprint(plan, lambda p: Path(p), {}) == before  # probe135 only
