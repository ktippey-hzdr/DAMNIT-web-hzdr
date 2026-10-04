# h5py's `Group.__getitem__` is typed as Group | Dataset | Datatype, so every
# `handle["entry/..."]` in an assertion needs narrowing pyright cannot infer.
# pyright: reportIndexIssue=false, reportAttributeAccessIssue=false
# pyright: reportArgumentType=false, reportOperatorIssue=false, reportCallIssue=false
# ruff: noqa: RUF001 -- the vendor writes the micro sign (U+00B5) in its sidecars
"""The h5py packs against shot-aligner's per-pack references (plan phase 2b).

`tests/fixtures/hzdr-reference/packs/<pack>.json` is what shot-aligner's own
pack writes for the reference fixture's acquisition into a fresh tree at
`entry/detector` (`make_pack_references.py` there): every link name, NX_class,
shape, dtype, units, plot attributes and every other attribute, plus values for
small datasets and a sha256 for large ones. Each DAMNIT pack must write the same
detector subtree for the same files. Re-vendor with
`hzdr/scripts/sync-hzdr-reference.sh --apply`; never edit the copy.

Ported from shot-aligner's `test_diagnostic_packs.py`, `test_data_files.py`
and `test_conditions.py` (pack and stack parts), adapted to h5py, are the
behavioural tests below; the reader tests are in `test_hzdr_pack_readers.py`.
"""

from __future__ import annotations

import hashlib
import json
import weakref
from pathlib import Path

import h5py
import numpy as np
import pytest
from PIL import Image

from damnit_api.metadata import hzdr_packs
from damnit_api.metadata.hzdr_packs import sequence_frames
from damnit_api.shared.hzdr_paths import map_path, parse_path_map

FIXTURE = Path(__file__).parent / "fixtures" / "hzdr-reference"
RAW = FIXTURE / "raw"
RECORDED_ROOT = "/bigdata/HPLexp/reference-fixture"
PACK_IDS = ("camera_png_csv", "spectrometer_irr8", "sequence_frames")

# Recorded by the manifest walk itself; everything else is under `attrs`.
WALKED = ("NX_class", "units", "signal", "axes", "default", "nds_definition")


def _reference(pack: str) -> dict:
    return json.loads((FIXTURE / "packs" / f"{pack}.json").read_text(encoding="utf-8"))


def _text(value):
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode()
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        return [_text(v) for v in value]
    return str(value)


def _attribute(value):
    if isinstance(value, bytes):
        return value.decode()
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        return [_attribute(v) for v in value]
    return value


def _value(dataset, small: int):
    if dataset.size > small:
        return None
    if dataset.dtype.kind in "SOU":
        data = dataset.asstr()[()]
        return data.tolist() if hasattr(data, "tolist") else data
    data = dataset[()]
    if isinstance(data, np.ndarray):
        return data.tolist()
    return data.item() if hasattr(data, "item") else data


def _digest(dataset, small: int):
    if dataset.size <= small or dataset.dtype.kind in "SOU":
        return None
    data = np.ascontiguousarray(dataset[()])
    little = data.astype(data.dtype.newbyteorder("<"))
    return hashlib.sha256(little.tobytes()).hexdigest()


def _link_node(link) -> dict | None:
    """A soft or external link, recorded by its target and not followed."""
    if isinstance(link, h5py.SoftLink):
        return {"type": "softlink", "link": link.path}
    if isinstance(link, h5py.ExternalLink):
        return {"type": "externallink", "link": f"{link.filename}:{link.path}"}
    return None


def _node(obj, parent, name: str, small: int) -> tuple[dict, bool]:
    """One object's entry, and whether its value is volatile."""
    is_group = isinstance(obj, h5py.Group)
    entry = {
        "type": "group" if is_group else "dataset",
        "NX_class": _text(obj.attrs.get("NX_class")),
    }
    if not is_group:
        entry["shape"] = list(obj.shape)
        entry["dtype"] = "string" if obj.dtype.kind in "SOU" else obj.dtype.str
        entry["units"] = _text(obj.attrs.get("units"))
    entry |= {
        attr: _text(obj.attrs[attr])
        for attr in ("signal", "axes", "default", "nds_definition")
        if attr in obj.attrs
    }
    extra = {
        key: _attribute(obj.attrs[key])
        for key in sorted(obj.attrs)
        if key not in WALKED
    }
    if extra:
        entry["attrs"] = extra
    if is_group:
        return entry, False
    if name == "date" and _text(parent.attrs.get("NX_class")) == "NXnote":
        return entry, True
    measured = {"value": _value(obj, small), "sha256": _digest(obj, small)}
    entry |= {key: value for key, value in measured.items() if value is not None}
    return entry, False


def walk(path: Path, small: int = 64) -> tuple[dict, list[str]]:
    """shot-aligner's manifest walk plus values, over a DAMNIT-written file.

    The same rules as `make_reference_fixture.manifest` and
    `make_pack_references`: every link name, aliases grouped with the
    lexicographically first as canonical, `attrs` for the rest, `value` for
    datasets of at most `small` elements, `sha256` above that, and an NXnote's
    `date` listed as volatile.
    """
    nodes: dict[str, dict] = {}
    objects: dict = {}
    volatile: list[str] = []

    def visit(group, prefix: str, ancestors: tuple) -> None:
        for name in sorted(group):
            here = f"{prefix}/{name}"
            linked = _link_node(group.get(name, getlink=True))
            if linked is not None:
                nodes[here] = linked
                continue
            obj = group[name]
            nodes[here], is_volatile = _node(obj, group, name, small)
            if is_volatile:
                volatile.append(here)
            objects.setdefault(obj.id, []).append(here)
            if isinstance(obj, h5py.Group) and obj.id not in ancestors:
                visit(obj, here, (*ancestors, obj.id))

    with h5py.File(path, "r") as handle:
        visit(handle, "", (handle.id,))
    for paths in objects.values():
        canonical = min(paths)
        for name in paths:
            if name != canonical:
                nodes[name]["link"] = canonical
    return dict(sorted(nodes.items())), volatile


def _write(tmp_path: Path, pack: str, acquisition: dict, read_path) -> tuple:
    """One acquisition through a DAMNIT pack at /entry/detector."""
    target = tmp_path / f"{pack}.h5"
    with h5py.File(target, "w") as handle:
        entry = handle.create_group("entry")
        entry.attrs["NX_class"] = "NXentry"
        detector = entry.create_group("detector")
        detector.attrs["NX_class"] = "NXdetector"
        problems = hzdr_packs.write(pack, detector, acquisition, read_path)
        default = detector.attrs.get("default")
        plottable = f"entry/detector/{_text(default)}" if default is not None else None
    return target, problems, plottable


def _from_raw(relative: str) -> Path:
    return RAW / relative


# ---------------------------------------------------------------------------
# Parity with shot-aligner's packs, node by node and value by value.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pack", PACK_IDS)
def test_the_pack_writes_what_shot_aligners_pack_writes(tmp_path, pack):
    reference = _reference(pack)
    acquisition = {
        **reference["acquisition"],
        "instrument": reference["instrument"]["label"],
    }
    path, problems, plottable = _write(tmp_path, pack, acquisition, _from_raw)
    nodes, volatile = walk(path, reference["small"])

    assert problems == reference["problems"]
    assert plottable == reference["plottable"]
    assert volatile == reference["volatile"]
    assert sorted(nodes) == sorted(reference["nodes"])
    for name, expected in reference["nodes"].items():
        assert nodes[name] == expected, name


@pytest.mark.parametrize("pack", PACK_IDS)
def test_the_events_files_mapped_onto_this_host_give_the_same_output(tmp_path, pack):
    """What DAMNIT will hold in phase 3: recorded /bigdata paths from the events."""
    events = [
        json.loads(line)
        for line in (FIXTURE / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    files = []
    for event in events:
        if event["metadata"]["instrument"]["format"] != pack:
            continue
        ref = event["payload_ref"]
        files += [m["path"] for m in ref.get("members") or [ref]]
    path_map = parse_path_map(f"{RECORDED_ROOT}={RAW.as_posix()}")
    reference = _reference(pack)
    acquisition = {"instrument": reference["instrument"]["label"], "files": files}
    path, problems, _ = _write(
        tmp_path, pack, acquisition, lambda f: Path(map_path(f, path_map))
    )
    nodes, _ = walk(path, reference["small"])
    assert problems == []
    assert nodes == reference["nodes"]


def test_every_fixture_pack_is_registered():
    assert set(hzdr_packs.PACKS) == set(PACK_IDS)
    with pytest.raises(KeyError, match="no pack"):
        hzdr_packs.write("nonesuch", None, {"files": []}, _from_raw)


# ---------------------------------------------------------------------------
# Streaming: memory bounded by a frame, not by a recording.
# ---------------------------------------------------------------------------


def _recording(tmp_path: Path, frames: int) -> list[str]:
    folder = tmp_path / "rec"
    folder.mkdir()
    for n in range(1, frames + 1):
        (folder / f"20cm_6kv_{n:05d}.tif").touch()
    return [f"rec/20cm_6kv_{n:05d}.tif" for n in range(1, frames + 1)]


def _peak_frames_held(tmp_path: Path, monkeypatch, frames: int) -> tuple[int, Path]:
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
    tmp_path.mkdir(parents=True, exist_ok=True)
    files = _recording(tmp_path, frames)
    out, problems, _ = _write(
        tmp_path,
        "sequence_frames",
        {"instrument": "Probe135", "files": files},
        lambda f: tmp_path / f,
    )
    assert problems == []
    return peak, out


def test_a_recording_is_streamed_one_frame_at_a_time(tmp_path, monkeypatch):
    peak, out = _peak_frames_held(tmp_path, monkeypatch, 60)
    # The frame being written and, at most, the one before it: never the recording.
    assert peak <= 2
    with h5py.File(out, "r") as handle:
        stack = handle["entry/detector/raw_data/image"]
        assert stack.shape == (60, 16, 12)
        assert stack.maxshape == (None, 16, 12)
        assert stack.chunks == (1, 16, 12)
        assert (stack.compression, stack.compression_opts) == ("gzip", 4)
        assert [int(stack[i, 0, 0]) for i in (0, 29, 59)] == [1, 30, 60]


def test_the_frames_held_do_not_grow_with_the_recording(tmp_path, monkeypatch):
    short, _ = _peak_frames_held(tmp_path / "a", monkeypatch, 3)
    long, _ = _peak_frames_held(tmp_path / "b", monkeypatch, 200)
    assert long == short


def test_a_camera_frame_is_a_chunked_gzip_dataset(tmp_path):
    reference = _reference("camera_png_csv")
    acquisition = {**reference["acquisition"], "instrument": "M1_Spec_Fib_Cer"}
    path, _, _ = _write(tmp_path, "camera_png_csv", acquisition, _from_raw)
    with h5py.File(path, "r") as handle:
        image = handle["entry/detector/raw_data/image"]
        assert image.chunks is not None
        assert (image.compression, image.compression_opts) == ("gzip", 4)


# ---------------------------------------------------------------------------
# Ported: the stack (test_conditions.StackTests).
# ---------------------------------------------------------------------------


def test_every_frame_in_order_with_the_unreadable_one_flagged(tmp_path, monkeypatch):
    names = [f"20cm_6kv_{n}.tif" for n in (10, 2, 1, 3)]
    ordered = sorted((Path(n) for n in names), key=sequence_frames.frame_order)
    assert [p.name for p in ordered][:2] == ["20cm_6kv_1.tif", "20cm_6kv_2.tif"]
    for name in names:
        (tmp_path / name).touch()

    def read(path: Path):
        number = sequence_frames.frame_number(path)
        return None if number == 3 else np.full((2, 3), number, dtype="uint16")

    monkeypatch.setattr(sequence_frames, "read_image", read)
    out, problems, plottable = _write(
        tmp_path,
        "sequence_frames",
        {"instrument": "Probe135", "files": names},
        lambda f: tmp_path / f,
    )
    assert plottable == "entry/detector/data"
    with h5py.File(out, "r") as handle:
        raw = handle["entry/detector/raw_data"]
        image = raw["image"][()]
        assert image.shape == (4, 2, 3)
        assert [int(image[i, 0, 0]) for i in range(4)] == [1, 2, 0, 10]
        assert list(raw["frame_number"][()]) == [1, 2, 3, 10]
        assert list(raw["frame_readable"][()]) == [True, True, False, True]
        assert "zeros" in raw["frame_readable"].attrs["description"]
    assert any("1 of 4 frames" in p for p in problems)


def test_unreadable_frames_before_the_first_readable_one_stay_zeros(
    tmp_path, monkeypatch
):
    names = [f"set1_{n:05d}.tif" for n in (1, 2, 3)]
    for name in names:
        (tmp_path / name).touch()

    def read(path: Path):
        number = sequence_frames.frame_number(path)
        return None if number == 1 else np.full((2, 2), number, dtype="uint16")

    monkeypatch.setattr(sequence_frames, "read_image", read)
    out, problems, _ = _write(
        tmp_path,
        "sequence_frames",
        {"instrument": "Probe135", "files": names},
        lambda f: tmp_path / f,
    )
    with h5py.File(out, "r") as handle:
        image = handle["entry/detector/raw_data/image"][()]
        assert [int(image[i, 0, 0]) for i in range(3)] == [0, 2, 3]
        readable = handle["entry/detector/raw_data/frame_readable"][()]
        assert list(readable) == [False, True, True]
    assert problems == ["1 of 3 frames could not be written: set1_00001.tif"]


def test_a_recording_with_no_readable_frame_writes_no_stack(tmp_path):
    names = ["20cm_6kv_00001.tif", "20cm_6kv_00002.tif"]
    for name in names:
        (tmp_path / name).write_bytes(b"not a tiff")
    out, problems, plottable = _write(
        tmp_path,
        "sequence_frames",
        {"instrument": "Probe135", "files": names},
        lambda f: tmp_path / f,
    )
    assert plottable is None
    assert problems == [
        "none of the 2 frames of this recording could be read (20cm_6kv_00001.tif …)"
    ]
    with h5py.File(out, "r") as handle:
        assert list(handle["entry/detector/raw_data"]) == []


def test_a_single_frame_keeps_its_ordinal_and_the_recorder_comment(tmp_path):
    pixels = np.arange(12, dtype=np.uint16).reshape(3, 4) * 1000
    Image.fromarray(pixels).save(tmp_path / "set10_00001.tif")
    (tmp_path / "set10_00001.tif.rec").write_bytes(
        (
            "pco.camware\r\n\r\nRecord Date: 20.02.2026 Time: 13:14:46\r\n"
            "Camera Settings\r\nExposure / Delay: 10 ms / 0 ms\r\n"
            "Serial Number: 1234\r\n"
        ).encode("utf-16")
    )
    out, problems, _ = _write(
        tmp_path,
        "sequence_frames",
        {
            "instrument": "Probe135",
            "seq": 1,
            "files": ["set10_00001.tif", "set10_00001.tif.rec"],
        },
        lambda f: tmp_path / f,
    )
    assert problems == []
    with h5py.File(out, "r") as handle:
        detector = handle["entry/detector"]
        np.testing.assert_array_equal(detector["raw_data/image"][()], pixels)
        assert detector["raw_data/sequence_number"][()] == 1
        assert detector["recording_started"].asstr()[()] == "2026-02-20T13:14:46"
        settings = detector["acquisition_settings"]
        assert settings["serial_number"].asstr()[()] == "1234"
        assert settings["serial_number"].attrs["source_text"] == "Serial Number: 1234"
        assert detector["count_time"][()] == pytest.approx(10.0)
        assert detector["count_time"].attrs["units"] == "ms"
        note = detector["original_metadata/recorder_comment"]
        assert note.attrs["NX_class"] == "NXnote"
        assert "Record Date" in note["data"].asstr()[()]


def test_a_missing_frame_is_a_problem_not_an_error(tmp_path):
    _, problems, plottable = _write(
        tmp_path,
        "sequence_frames",
        {"instrument": "Probe135", "files": ["gone_00001.tif"]},
        lambda f: tmp_path / f,
    )
    assert problems == ["missing frame for Probe135"]
    assert plottable is None


# ---------------------------------------------------------------------------
# Ported: the camera and spectrometer packs (test_data_files.BuildTests,
# GovernedKeyTests, test_diagnostic_packs.BmpFrameTests).
# ---------------------------------------------------------------------------

IRR8_HEADER = (
    "\nIntegration time [ms]: 35,000\nAveraging Nr. [scans]: 1\n"
    "Smoothing Nr. [pixels]: 0\nData measured with spectrometer [name]: demo\n"
    "Wave;Sample;Dark;Reference;Absolute irradiance;Photon counts\n"
    "[nm];[counts];[counts];[counts];[uW/cm2/nm];[counts]\n\n"
)
IRR8_ROWS = "500,0;10;2;0;8;4\n501,0;20;3;0;17;9\n"

ANALYSIS_HEADER = "Tool;" + "Name;Value;Unit;" * 26 + "\n"
PEAK_PROFILE = (
    "Peak Profile;Pos x;0.084;mm;Pos y;0.064;mm;ROI;inv. Polygon;;"
    "Peak Determination Method;Calculate Centroid;a.u.;Averaging;1;Pixel;"
    "FWHM horiz;0.00872;mm;1/e² horiz;0.01482;mm;µ horiz;0.08431;mm;"
    "Amplitude horiz;21328.1;a.u.;Offset horiz;1778.2;a.u.;"
    "FWHM vert;0.00852;mm;1/e² vert;0.01447;mm;µ vert;1542.9;mm;"
    "Amplitude vert;{vert};a.u.;Offset vert;457.9;a.u.\n"
)
STATISTICS = "Statistics;Sum;900;a.u.;Max;3500;a.u.;ROI;Image;\n"


@pytest.fixture
def camera(tmp_path):
    pixels = np.arange(36, dtype=np.uint16).reshape(6, 6) * 100
    Image.fromarray(pixels).save(tmp_path / "frame_original.png")
    (tmp_path / "frame.csv").write_text(
        "\n" * 12 + "Name;Camera;Label;beam\nComment;test\n", encoding="cp1252"
    )
    return tmp_path, pixels


def _camera(root: Path, files=("frame_original.png", "frame.csv"), **extra):
    acquisition = {"instrument": "Camera", "files": list(files), "seq": 6, **extra}
    return _write(root, "camera_png_csv", acquisition, lambda f: root / f)


def _spectrum(root: Path, name="device_01Dez25_120000_0001.Irr8.txt"):
    acquisition = {"instrument": "Spectrum", "files": [name]}
    return _write(root, "spectrometer_irr8", acquisition, lambda f: root / f)


def test_camera_metadata_retains_arbitrary_keys_and_original_fields(camera):
    root, pixels = camera
    (root / "frame.csv").write_text(
        "\n" * 12 + "Name;Camera;Label;beam\nComment;alignment\n"
        "Exposure;0.035000 s;Gain;100.00 dB\nGamma;1.00;Chip Size X;3.69 mm\n"
        "Flip V;TRUE;Stretch X;1.2\nA B;first;A/B;second\nChip Size Y;unknown\n",
        encoding="cp1252",
    )
    out, problems, _ = _camera(root)
    assert any("Chip Size Y" in p for p in problems)
    with h5py.File(out, "r") as handle:
        detector = handle["entry/detector"]
        original = json.loads(detector["original_metadata/data"].asstr()[()])
        assert original["A B"] == "first"
        assert original["A/B"] == "second"
        assert original["Flip V"] == "TRUE"
        assert detector["original_metadata/source_file"].asstr()[()] == "frame.csv"
        assert detector["metadata/exposure"].asstr()[()] == "0.035000 s"
        exposure = detector["acquisition_settings/exposure"]
        assert exposure[()] == pytest.approx(0.035)
        assert exposure.attrs["units"] == "s"
        assert exposure.attrs["source_text"] == "0.035000 s"
        assert "chip_size_y" not in detector["acquisition_settings"]
        np.testing.assert_array_equal(detector["raw_data/image"][()], pixels)
        assert detector["data/image"].id == detector["raw_data/image"].id


def test_base_class_fields_come_from_settings_and_the_frame_shape(camera):
    root, _ = camera
    (root / "frame.csv").write_text(
        "\n" * 12 + "Name;Camera\nExposure;0.035000 s\nGain;12.00 dB\n"
        "Chip Size X;3.69 mm;Chip Size Y;2.77 mm\n",
        encoding="cp1252",
    )
    out, _, _ = _camera(root)
    with h5py.File(out, "r") as handle:
        detector = handle["entry/detector"]
        assert detector["count_time"][()] == pytest.approx(0.035)
        assert detector["count_time"].attrs["units"] == "s"
        assert detector["gain_setting"][()] == pytest.approx(12.0)
        assert detector["gain_setting"].attrs["units"] == "dB"
        assert detector["x_pixel_size"][()] == pytest.approx(3.69 / 6 * 1000)
        assert detector["y_pixel_size"][()] == pytest.approx(2.77 / 6 * 1000)
        assert detector["x_pixel_size"].attrs["units"] == "um"


def test_a_frame_without_a_chip_size_gets_no_invented_pixel_size(camera):
    root, _ = camera
    (root / "frame.csv").write_text(
        "\n" * 12 + "Name;Camera\nExposure;0.035000 s\n", encoding="cp1252"
    )
    out, _, _ = _camera(root)
    with h5py.File(out, "r") as handle:
        detector = handle["entry/detector"]
        assert "x_pixel_size" not in detector
        assert "count_time" in detector


def test_a_missing_sidecar_keeps_the_frame(camera):
    root, pixels = camera
    (root / "frame.csv").unlink()
    out, problems, plottable = _camera(root)
    assert problems == ["missing CSV frame.csv"]
    assert plottable == "entry/detector/data"
    with h5py.File(out, "r") as handle:
        np.testing.assert_array_equal(
            handle["entry/detector/raw_data/image"][()], pixels
        )


def test_a_csv_only_acquisition_is_reported(camera):
    root, _ = camera
    out, problems, plottable = _camera(root, files=["frame.csv"])
    assert problems == ["missing image for CSV-only acquisition"]
    assert plottable is None
    with h5py.File(out, "r") as handle:
        assert handle["entry/detector/fabrication/model"].asstr()[()] == "Camera"


def test_an_unreadable_frame_and_a_conflicting_csv_are_problems(camera):
    root, _ = camera
    (root / "frame_original.png").write_bytes(b"not a png")
    (root / "frame.csv").write_text("\n" * 12 + "Name;A\nName;B\n", encoding="cp1252")
    _, problems, plottable = _camera(root)
    assert plottable is None
    assert problems[0].startswith("unreadable CSV frame.csv: Conflicting")
    assert problems[1] == "unreadable image frame_original.png"


def test_the_beam_profile_fit_and_roi_statistics_are_typed(camera):
    root, _ = camera
    block = ANALYSIS_HEADER + PEAK_PROFILE.format(vert="21572.4") + STATISTICS
    (root / "frame.csv").write_text(
        block + "Profile;\n" * 9 + "\nName;Camera\n", encoding="cp1252"
    )
    out, problems, _ = _camera(root, when="2025-12-01T12:00:00")
    assert problems == []
    with h5py.File(out, "r") as handle:
        fit = handle["entry/detector/beam_profile_fit"]
        assert fit.attrs["NX_class"] == "NXprocess"
        assert fit["fwhm_horiz"][()] == pytest.approx(0.00872)
        assert fit["fwhm_horiz"].attrs["units"] == "mm"
        assert fit["width_1e2_horiz"][()] == pytest.approx(0.01482)
        assert fit["date"].asstr()[()] == "2025-12-01T12:00:00"
        assert fit["sequence_index"][()] == 1
        assert not fit["fit_is_degenerate"][()]
        assert fit["fit_axis_amplitude_ratio"][()] == pytest.approx(1.011, abs=1e-3)
        assert fit["parameters/roi"].asstr()[()] == "inv. Polygon"
        assert "FULL WIDTHS" in fit["notes"].asstr()[()]
        stats = handle["entry/detector/roi_statistics"]
        assert stats["sum"][()] == 900
        assert stats["parameters/roi"].asstr()[()] == "Image"
        analysis = json.loads(handle["entry/detector/original_metadata/analysis"][()])
        assert analysis["Peak Profile"]["µ horiz"]["value"] == "0.08431"


def test_a_degenerate_fit_is_written_as_exported_and_flagged(camera):
    root, _ = camera
    block = ANALYSIS_HEADER + PEAK_PROFILE.format(vert="0") + STATISTICS
    (root / "frame.csv").write_text(
        block + "Profile;\n" * 9 + "\nName;Camera\n", encoding="cp1252"
    )
    out, problems, _ = _camera(root)
    assert any("beam-profile fit is degenerate" in p for p in problems)
    with h5py.File(out, "r") as handle:
        fit = handle["entry/detector/beam_profile_fit"]
        assert fit["fit_is_degenerate"][()]
        assert "at or below zero" in fit["fit_is_degenerate"].attrs["description"]
        assert "fit_axis_amplitude_ratio" not in fit


def test_a_name_only_bmp_keeps_the_pixels_and_says_what_is_missing(tmp_path):
    name = "Set1_2024-08-28_10h-00m-18s_32.bmp"
    pixels = (np.arange(48 * 64, dtype=np.uint16).reshape(48, 64) % 70).astype(np.uint8)
    Image.fromarray(pixels).save(tmp_path / name)
    out, problems, plottable = _write(
        tmp_path,
        "camera_png_csv",
        {
            "instrument": "Ar profiler",
            "files": [name],
            "label": "Set1",
            "seq": 32,
            "when": "2024-08-28T10:00:18",
        },
        lambda f: tmp_path / f,
    )
    assert problems == []
    assert plottable == "entry/detector/data"
    with h5py.File(out, "r") as handle:
        detector = handle["entry/detector"]
        image = detector["raw_data/image"]
        assert image.dtype == np.uint8
        np.testing.assert_array_equal(image[()], pixels)
        assert image.attrs["source_format"] == "bmp"
        assert image.attrs["source_file"] == name
        assert int(detector["raw_data/sequence_number"][()]) == 32
        assert detector["raw_data/sequence_number"].attrs["source"] == "file name"
        assert detector["raw_data/name"].asstr()[()] == "Set1"
        assert "absent, not zero" in detector["metadata_limitations/data"].asstr()[()]
        for missing in (
            "metadata",
            "fabrication",
            "acquisition_settings",
            "original_metadata",
            "count_time",
        ):
            assert missing not in detector


def test_a_bmp_beside_its_16_bit_original_is_not_the_frame(camera):
    root, pixels = camera
    Image.fromarray(np.zeros((2, 2), dtype=np.uint8)).save(root / "frame.bmp")
    out, _, _ = _camera(root, files=["frame.bmp", "frame.csv", "frame_original.png"])
    with h5py.File(out, "r") as handle:
        np.testing.assert_array_equal(
            handle["entry/detector/raw_data/image"][()], pixels
        )
        assert "source_format" not in handle["entry/detector/raw_data/image"].attrs


def test_a_spectrum_asks_to_be_opened_logarithmic_and_a_frame_does_not(camera):
    root, _ = camera
    (root / "device_01Dez25_120000_0001.Irr8.txt").write_text(
        IRR8_HEADER + IRR8_ROWS, encoding="cp1252"
    )
    spectrum, problems, plottable = _spectrum(root)
    frame, _, _ = _camera(root)
    assert problems == []
    assert plottable == "entry/detector/data"
    with h5py.File(spectrum, "r") as handle:
        data = handle["entry/detector/data"]
        assert json.loads(data.attrs["SILX_style"]) == {"signal_scale_type": "log"}
        assert data.attrs["signal"] == "absolute_irradiance"
        assert list(data.attrs["axes"]) == ["wave"]
        np.testing.assert_array_equal(data["wave"][()], [500, 501])
        assert data["wave"].id == handle["entry/detector/raw_data/wave"].id
        np.testing.assert_array_equal(
            handle["entry/detector/raw_data/sample"][()], [10, 20]
        )
        assert handle["entry/detector/count_time"][()] == pytest.approx(35.0)
        assert handle["entry/detector/count_time"].attrs["units"] == "ms"
        name = handle["entry/detector/metadata/Data_measured_with_spectrometer"]
        assert "units" not in name.attrs
        serial = handle["entry/detector/fabrication/serial_number"]
        assert serial.asstr()[()] == "device"
    with h5py.File(frame, "r") as handle:
        assert "SILX_style" not in handle["entry/detector/data"].attrs


def test_an_unreadable_or_missing_spectrum_is_a_problem(tmp_path):
    (tmp_path / "device_01Dez25_120000_0001.Irr8.txt").write_text("bad")
    out, problems, plottable = _spectrum(tmp_path)
    assert plottable is None
    name = "device_01Dez25_120000_0001.Irr8.txt"
    reason = f"Incomplete IRR8 header in {tmp_path / name}"
    assert problems == [f"Spectrum: could not read {name}: {reason}"]
    _, problems, _ = _spectrum(tmp_path, name="other.Irr8.txt")
    assert problems == ["missing Irr8 file for Spectrum"]
    with h5py.File(out, "r") as handle:
        assert list(handle["entry/detector"]) == []


def test_a_spectrum_without_a_wavelength_is_stored_raw(tmp_path):
    header = IRR8_HEADER.replace("Wave;", "Pixel;").replace("[nm];", "[px];")
    (tmp_path / "s.Irr8.txt").write_text(header + IRR8_ROWS, encoding="cp1252")
    out, problems, plottable = _spectrum(tmp_path, name="s.Irr8.txt")
    assert plottable is None
    assert problems == [
        "Spectrum: no wavelength/irradiance pair to plot; the columns are stored raw"
    ]
    with h5py.File(out, "r") as handle:
        assert "pixel" in handle["entry/detector/raw_data"]
        assert "data" not in handle["entry/detector"]
