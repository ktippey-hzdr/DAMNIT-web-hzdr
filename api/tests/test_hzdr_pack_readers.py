# ruff: noqa: RUF001 -- the vendor writes the micro sign (U+00B5) in its sidecars
"""The vendored readers and the Pillow frame reader the h5py packs read through.

Ported from shot-aligner's `test_data_files.py` (`DataFileTests`,
`AnalysisBlockTests`): the readers are vendored byte for byte, so these are the
same assertions run against DAMNIT's copy. The frame reader is DAMNIT's own
(Pillow instead of OpenCV) and is held to OpenCV's `IMREAD_UNCHANGED` output:
the fixture's real camera PNG through its pack reference's sha256 in
`test_hzdr_packs.py`, the layouts OpenCV gives each mode here.
"""

from __future__ import annotations

import subprocess  # noqa: S404 -- the interpreter itself, fixed arguments
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from damnit_api.metadata.hzdr_packs._images import read_image
from damnit_api.metadata.hzdr_packs.vendor import camera_metadata
from damnit_api.metadata.hzdr_packs.vendor.img_csv import read_csv_metadata_file
from damnit_api.metadata.hzdr_packs.vendor.irr8 import read_irr8

VENDOR = (
    Path(__file__).parents[1]
    / "src"
    / "damnit_api"
    / "metadata"
    / "hzdr_packs"
    / "vendor"
)
FIXTURE = Path(__file__).parent / "fixtures" / "hzdr-reference"

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
    "Amplitude vert;21572.4;a.u.;Offset vert;457.9;a.u.\n"
)
STATISTICS = "Statistics;Sum;900;a.u.;Max;3500;a.u.;ROI;Image;\n"


def analysis_csv(settings: str) -> str:
    return (
        ANALYSIS_HEADER + PEAK_PROFILE + STATISTICS + "Profile;\n" * 9 + "\n" + settings
    )


# ---- the vendored readers (DataFileTests) ---------------------------------


def test_csv_empty_values_keep_pairs_and_quoted_comments(tmp_path):
    path = tmp_path / "camera.csv"
    path.write_text(
        "\n" * 12 + 'Comment;;Name;Camera;\nLabel;"beam; profile";Gain;100.00 dB\n',
        encoding="cp1252",
    )
    assert read_csv_metadata_file(path) == {
        "Comment": "",
        "Name": "Camera",
        "Label": "beam; profile",
        "Gain": "100.00 dB",
    }
    path.write_text("\n" * 12 + "Name;A\nName;B\n", encoding="cp1252")
    with pytest.raises(ValueError, match="Conflicting"):
        read_csv_metadata_file(path)


def test_spectrum_columns_are_numeric_and_complete(tmp_path):
    path = tmp_path / "spectrum.Irr8.txt"
    path.write_text(IRR8_HEADER + IRR8_ROWS, encoding="cp1252")
    data = read_irr8(path)
    assert data["wave"] == {"data": [500, 501], "units": "nm"}
    assert data["sample"]["data"] == [10, 20]
    assert data["Data measured with spectrometer"] == {"value": "demo", "units": None}
    assert data["Integration time"] == {"value": 35.0, "units": "ms"}
    for payload in ("500;1;2\n", "500;1;2;3;4;5;6\n", "500;nan;2;3;4;5\n", ""):
        path.write_text(IRR8_HEADER + payload, encoding="cp1252")
        with pytest.raises(ValueError):  # noqa: PT011 -- every malformed shape
            read_irr8(path)


def test_typed_settings_preserve_units_and_reject_guesses():
    typed, problems = camera_metadata.typed_settings({
        "Exposure": "3,5e-2 s",
        "Gain": "100.00 dB",
        "Gamma": "1.00",
        "Chip Size X": "3690 µm",
        "Chip Size Y": "2.77",
        "Black Level Offset": "NaN",
    })
    assert typed["exposure"]["value"] == pytest.approx(0.035)
    assert typed["gain"]["units"] == "dB"
    assert typed["chip_size_x"]["value"] == 3690
    assert typed["chip_size_x"]["units"] == "µm"
    assert typed["gamma"]["units"] == ""
    assert len(problems) == 2
    assert "chip_size_y" not in typed
    for text in ("1e999 s", "automatic", "0.1", "10 furlongs"):
        parsed, warnings = camera_metadata.typed_settings({"Exposure": text})
        assert not parsed
        assert len(warnings) == 1


def test_the_vendored_readers_work_without_optional_libraries(tmp_path):
    """The readers stay stdlib-importable, as shot-aligner's CI holds them."""
    csv_path = tmp_path / "camera.csv"
    csv_path.write_text("\n" * 12 + "Name;Camera\n", encoding="cp1252")
    spectrum = tmp_path / "spectrum.Irr8.txt"
    spectrum.write_text(IRR8_HEADER + IRR8_ROWS, encoding="cp1252")
    code = """
import builtins, importlib.util, sys
sys.dont_write_bytecode = True
original_import = builtins.__import__
def checked_import(name, *args, **kwargs):
    blocked = {'numpy', 'pandas', 'cv2', 'matplotlib', 'nexusformat', 'h5py', 'PIL'}
    if name.split('.')[0] in blocked:
        raise AssertionError('unexpected parser dependency: ' + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = checked_import
def load(name):
    path = sys.argv[1] + '/' + name + '.py'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
assert load('img_csv').read_csv_metadata_file(sys.argv[2]) == {'Name': 'Camera'}
assert load('irr8').read_irr8(sys.argv[3])['sample']['data'] == [10, 20]
load('camera_metadata')
"""
    run = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", code, str(VENDOR), str(csv_path), str(spectrum)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr


# ---- the vendor analysis block (AnalysisBlockTests) -----------------------


def test_block_is_parsed_and_the_settings_below_still_read(tmp_path):
    path = tmp_path / "frame.csv"
    path.write_text(
        analysis_csv("Name;Camera\nExposure;0.035000 s\n"), encoding="cp1252"
    )
    blocks = camera_metadata.analysis_blocks(path)
    assert sorted(blocks) == ["Peak Profile", "Statistics"]
    assert blocks["Peak Profile"]["FWHM horiz"] == ("0.00872", "mm")
    assert read_csv_metadata_file(path)["Name"] == "Camera"


def test_a_file_without_the_block_yields_nothing_rather_than_guessing(tmp_path):
    path = tmp_path / "frame.csv"
    path.write_text("\n" * 12 + "Name;Camera\n", encoding="cp1252")
    assert camera_metadata.analysis_blocks(path) == {}


def test_the_fit_keeps_vendor_units_and_drops_the_centre_columns(tmp_path):
    path = tmp_path / "frame.csv"
    path.write_text(analysis_csv("Name;Camera\n"), encoding="cp1252")
    values, parameters, problems = camera_metadata.beam_profile_fit(
        camera_metadata.analysis_blocks(path)
    )
    assert problems == []
    assert values["fwhm_horiz"] == {
        "value": 0.00872,
        "units": "mm",
        "source_key": "FWHM horiz",
        "source_text": "0.00872",
    }
    assert values["width_1e2_horiz"]["value"] == pytest.approx(0.01482)
    assert not [name for name in values if "mu" in name or "centre" in name]
    assert parameters["roi"] == "inv. Polygon"
    assert parameters["peak_determination_method"] == "Calculate Centroid"


def test_a_position_only_block_yields_no_fit(tmp_path):
    path = tmp_path / "frame.csv"
    path.write_text(
        ANALYSIS_HEADER
        + "Peak Profile;Pos x;299;Pixel;Pos y;477;Pixel;ROI;Image;\n"
        + "Profile;\n" * 10
        + "\nName;Camera\n",
        encoding="cp1252",
    )
    values, parameters, problems = camera_metadata.beam_profile_fit(
        camera_metadata.analysis_blocks(path)
    )
    assert (values, problems) == ({}, [])
    assert parameters["roi"] == "Image"


# ---- the frame reader: OpenCV IMREAD_UNCHANGED, through Pillow ------------

RNG = np.random.default_rng(7)
GREY16 = RNG.integers(0, 65535, (7, 9), dtype=np.uint16)
GREY8 = GREY16.astype(np.uint8)
RGB8 = RNG.integers(0, 255, (7, 9, 3), dtype=np.uint8)
RGBA8 = RNG.integers(0, 255, (7, 9, 4), dtype=np.uint8)


@pytest.mark.parametrize("suffix", [".png", ".tif"])
def test_a_16_bit_grey_frame_is_uint16_unchanged(tmp_path, suffix):
    path = tmp_path / f"frame{suffix}"
    Image.fromarray(GREY16).save(path)
    image = read_image(path)
    assert image is not None
    assert image.dtype == np.uint16
    np.testing.assert_array_equal(image, GREY16)


def test_a_big_endian_tiff_comes_back_in_native_order(tmp_path):
    path = tmp_path / "frame.tif"
    big = Image.new("I;16B", (9, 7))
    big.frombytes(GREY16.astype(">u2").tobytes())
    big.save(path)
    image = read_image(path)
    assert image is not None
    assert image.dtype == np.dtype("uint16")
    assert image.dtype.isnative
    np.testing.assert_array_equal(image, GREY16)


@pytest.mark.parametrize("suffix", [".png", ".tif", ".bmp"])
def test_an_8_bit_grey_frame_is_uint8_unchanged(tmp_path, suffix):
    path = tmp_path / f"frame{suffix}"
    Image.fromarray(GREY8).save(path)
    image = read_image(path)
    assert image is not None
    assert image.dtype == np.uint8
    np.testing.assert_array_equal(image, GREY8)


@pytest.mark.parametrize("suffix", [".png", ".tif", ".bmp"])
def test_colour_comes_back_in_opencvs_bgr_order(tmp_path, suffix):
    path = tmp_path / f"frame{suffix}"
    Image.fromarray(RGB8).save(path)
    image = read_image(path)
    assert image is not None
    np.testing.assert_array_equal(image, RGB8[..., ::-1])


def test_alpha_comes_back_as_bgra(tmp_path):
    path = tmp_path / "frame.png"
    Image.fromarray(RGBA8).save(path)
    image = read_image(path)
    assert image is not None
    np.testing.assert_array_equal(image, RGBA8[..., [2, 1, 0, 3]])


def test_grey_with_alpha_is_expanded_to_bgra(tmp_path):
    path = tmp_path / "frame.png"
    Image.fromarray(np.stack([GREY8, GREY8 // 2], axis=-1), mode="LA").save(path)
    image = read_image(path)
    assert image is not None
    assert image.shape == (7, 9, 4)
    np.testing.assert_array_equal(image[..., 0], GREY8)
    np.testing.assert_array_equal(image[..., 2], GREY8)
    np.testing.assert_array_equal(image[..., 3], GREY8 // 2)


def test_a_palette_png_is_expanded_and_a_bilevel_one_is_0_or_255(tmp_path):
    palette = tmp_path / "palette.png"
    Image.fromarray(GREY8).convert("P").save(palette)
    image = read_image(palette)
    assert image is not None
    assert image.shape == (7, 9, 3)
    bilevel = tmp_path / "bilevel.png"
    Image.fromarray(GREY8 > 100).save(bilevel)
    image = read_image(bilevel)
    assert image is not None
    assert image.dtype == np.uint8
    np.testing.assert_array_equal(image, np.where(GREY8 > 100, 255, 0))


def test_a_float_tiff_is_float32(tmp_path):
    path = tmp_path / "frame.tif"
    values = RNG.random((5, 6)).astype(np.float32)
    Image.fromarray(values).save(path)
    image = read_image(path)
    assert image is not None
    assert image.dtype == np.float32
    np.testing.assert_array_equal(image, values)


def test_a_16_bit_colour_png_is_refused_rather_than_truncated(tmp_path):
    """Pillow would drop the low byte of each channel; OpenCV would not."""
    path = tmp_path / "deep.png"
    Image.fromarray(RGB8).save(path)
    data = bytearray(path.read_bytes())
    data[24] = 16  # IHDR bit depth: the header alone decides the refusal
    path.write_bytes(bytes(data))
    assert read_image(path) is None


def test_what_will_not_decode_is_none(tmp_path):
    path = tmp_path / "frame.png"
    path.write_bytes(b"not a png at all")
    assert read_image(path) is None
    assert read_image(tmp_path / "absent.png") is None


def test_the_fixtures_real_frames_read_as_the_reference_says():
    camera = read_image(
        FIXTURE
        / "raw"
        / "M1_Spec_Fib_Cer"
        / "set1_2025-12-01_15h-59m-04s_2_original.png"
    )
    assert camera is not None
    assert (camera.dtype, camera.shape) == (np.uint16, (494, 659))
    frame = read_image(FIXTURE / "raw" / "Probe135" / "20cm_6kv_00002.tif")
    assert frame is not None
    np.testing.assert_array_equal(frame, np.full((4, 5), 200, dtype=np.uint16))
