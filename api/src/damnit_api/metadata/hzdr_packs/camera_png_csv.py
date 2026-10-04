"""Camera exports: a 16-bit PNG frame and the acquisition software's CSV sidecar.

A port of shot-aligner's ``diagnostics/camera_png_csv.py`` ``write`` to h5py.
The output is the same, node for node; what is not carried over is the
``display`` copy (decision 6: each detector keeps only its default plot, which
is ``data``) and the ``claim`` half, which names files on a share DAMNIT never
walks -- the watchdog event already says which files belong together.

The frame is read through :func:`._images.read_image` (Pillow, laid out as
OpenCV's ``IMREAD_UNCHANGED``) and the sidecar through the vendored
``img_csv`` and ``camera_metadata``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from . import _h5
from ._images import read_image
from .vendor import bmp_frame_suffixes, is_measurement
from .vendor.camera_metadata import (
    analysis_blocks,
    beam_profile_fit,
    fit_quality,
    roi_statistics,
    typed_settings,
)
from .vendor.img_csv import read_csv_metadata_file

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import h5py

PACK = "camera_png_csv"

# Shot-aligner's wording, unchanged: what a name-only BMP frame does not carry.
NAME_ONLY_LIMITATIONS = (
    "8-bit BMP export with no sidecar. The label, date, time and sequence "
    "number come from the file name, and nothing else about this acquisition "
    "was recorded with it: exposure, gain, black level, gamma, chip size, pixel "
    "size, camera model and the vendor's analysis tools are absent, not zero. "
    "The export is 8-bit, so if the camera digitised more bits than that they "
    "were not kept, and its counts are not comparable with a 16-bit frame's. "
    "Included because the campaign config opts this instrument in with "
    "bmpFrames."
)

BEAM_FIT_NOTES = (
    "The vendor's 1/e^2 columns are stored here under their own name, "
    "width_1e2_*, and are FULL WIDTHS: across this campaign the ratio "
    "1/e^2 divided by FWHM is 1.6986, which is sqrt(2/ln2). They are "
    "deliberately NOT written to laser.beam_waist_x/y "
    "(/entry/instrument/laser/beam/beam_waist_*_1e2_radius), which is "
    "a RADIUS -- that mapping needs a halving and an operator ruling "
    "on which plane this camera images. See governed-keys.md."
)

# CSV key -> dataset name under `metadata`, kept as the vendor's text.
METADATA_FIELDS = (
    ("Black Level Offset", "black_level_offset"),
    ("Chip Size X", "chip_size_x"),
    ("Chip Size Y", "chip_size_y"),
    ("Exposure", "exposure"),
    ("Gain", "gain"),
    ("Gamma", "gamma"),
)


def bmp_frame(files: list[Path]) -> Path | None:
    """The opted-in BMP carrying the frame, by the manifest's preference order.

    A plain ``.bmp`` counts only when no longer BMP suffix also matches, so it
    never claims an ``_original.bmp``.
    """
    suffixes = bmp_frame_suffixes(PACK)
    for suffix in suffixes:
        for f in files:
            lowered = f.name.lower()
            if not lowered.endswith(suffix.lower()):
                continue
            longer = [
                s
                for s in suffixes
                if len(s) > len(suffix) and lowered.endswith(s.lower())
            ]
            if not longer:
                return f
    return None


def _typed(parent: h5py.Group, name: str, setting: dict, **attrs) -> h5py.Dataset:
    """A typed setting: the number, its unit, where it came from."""
    return _h5.field(
        parent,
        name,
        setting["value"],
        units=setting["units"] or None,
        source_key=setting["source_key"],
        source_text=setting["source_text"],
        **attrs,
    )


def _write_name_only(group: h5py.Group, acquisition: Mapping, bmp: Path, shape) -> None:
    """The fields a BMP frame's name supports, and the note on what it lacks."""
    raw = _h5.subgroup(group, "raw_data")
    seq = acquisition.get("seq")
    _h5.field(
        raw, "sequence_number", seq if seq is not None else "", source="file name"
    )
    _h5.field(raw, "name", acquisition.get("label", ""), source="file name")
    if shape is not None:
        _h5.dataset(raw, "image").attrs["source_format"] = "bmp"
        _h5.dataset(raw, "image").attrs["source_file"] = bmp.name
    _h5.note(
        group,
        "metadata_limitations",
        type="text/plain",
        description="what this frame's metadata is, and what it is not",
        data=NAME_ONLY_LIMITATIONS,
    )


def _write_frame(group: h5py.Group, frame: Path | None, problems: list[str]):
    """The measurement, its NXdata view and the detector's default; its shape."""
    _h5.group(group, "raw_data", "NXcollection")
    if frame is None:
        problems.append("missing image for CSV-only acquisition")
        return None
    if not frame.is_file():
        problems.append(f"missing image {frame.name}")
        return None
    pixels = read_image(frame)
    if pixels is None:
        problems.append(f"unreadable image {frame.name}")
        return None
    shape = pixels.shape
    stored = _h5.image(_h5.subgroup(group, "raw_data"), "image", pixels, units="counts")
    del pixels
    data = _h5.group(group, "data", "NXdata")
    _h5.link(data, "image", stored)
    data.attrs["signal"] = "image"
    stored.attrs["interpretation"] = "image"
    group.attrs["default"] = "data"
    return shape


def _write_sidecar(
    group: h5py.Group, metadata: dict, csv: Path | None, problems: list[str]
) -> dict:
    """The CSV's settings: as text, verbatim, and typed. Returns the typed ones."""
    raw = _h5.subgroup(group, "raw_data")
    _h5.field(raw, "sequence_number", metadata.get("Image No.", ""))
    _h5.field(raw, "name", metadata.get("Label", ""))
    fabrication = _h5.group(group, "fabrication", "NXfabrication")
    _h5.field(fabrication, "model", metadata.get("Name", ""))
    collection = _h5.group(group, "metadata", "NXcollection")
    for key, name in METADATA_FIELDS:
        _h5.field(collection, name, metadata.get(key, ""))
    _h5.field(collection, "comment", metadata.get("Comment", ""))
    original = {
        "type": "application/json",
        "data": json.dumps(metadata, ensure_ascii=False),
        "description": "original parsed camera CSV metadata; values kept as text",
    }
    if csv:
        original["source_file"] = csv.name
    _h5.note(group, "original_metadata", **original)

    typed, conversion_problems = typed_settings(metadata)
    problems.extend(conversion_problems)
    settings = _h5.group(group, "acquisition_settings", "NXcollection")
    for name, setting in typed.items():
        _typed(settings, name, setting)
    return typed


def _write_base_class_fields(group: h5py.Group, typed: dict, shape) -> None:
    """NXdetector's standard names for values held under vendor labels."""
    if "exposure" in typed:
        _typed(group, "count_time", {**typed["exposure"], "source_key": "Exposure"})
    if "gain" in typed:
        gain = typed["gain"]
        _h5.field(
            group, "gain_setting", gain["value"], units=gain["units"], source_key="Gain"
        )
    if shape is not None and len(shape) == 2:
        for chip, axis, pixels in (
            ("chip_size_x", "x", shape[1]),
            ("chip_size_y", "y", shape[0]),
        ):
            setting = typed.get(chip)
            if not setting or not pixels or setting["units"] != "mm":
                continue
            _h5.field(
                group,
                f"{axis}_pixel_size",
                setting["value"] / pixels * 1000.0,
                units="um",
                description=(
                    f"derived: {setting['source_text']} divided by {pixels} pixels"
                ),
            )


def _tool(
    group: h5py.Group,
    name: str,
    program: str,
    acquisition: Mapping,
    typed: tuple[dict, dict],
) -> h5py.Group:
    """One analysis tool as an NXprocess: typed values and its parameters."""
    values, texts = typed
    process = _h5.group(group, name, "NXprocess")
    _h5.field(process, "program", program)
    _h5.field(process, "date", acquisition.get("when", ""))
    _h5.field(process, "sequence_index", 1)
    for key, setting in values.items():
        _typed(process, key, setting)
    parameters = _h5.group(process, "parameters", "NXparameters")
    for key, text in texts.items():
        _h5.field(parameters, key, text)
    return process


def _blocks(group: h5py.Group, csv: Path | None, problems: list[str]) -> dict:
    """The vendor's analysis block, also kept whole as text."""
    blocks = {}
    if csv and csv.is_file():
        try:
            blocks = analysis_blocks(csv)
        except (ValueError, OSError) as error:
            problems.append(f"unreadable analysis block in {csv.name}: {error}")
    if blocks:
        raw = {
            tool: {
                name: {"value": text, "units": unit}
                for name, (text, unit) in fields.items()
            }
            for tool, fields in blocks.items()
        }
        _h5.field(
            _h5.subgroup(group, "original_metadata"),
            "analysis",
            json.dumps(raw, ensure_ascii=False),
            description=(
                "original parsed camera analysis-tool block; values kept as text"
            ),
        )
    return blocks


def _flag_fit(
    process: h5py.Group, fit: dict, acquisition: Mapping, problems: list[str]
) -> None:
    """Say whether the fit is worth believing; the values stay as exported."""
    quality = fit_quality(fit)
    if not quality:
        return
    flag = _h5.field(process, "fit_is_degenerate", bool(quality["degenerate"]))
    if quality["axis_ratio"] is not None:
        _h5.field(process, "fit_axis_amplitude_ratio", round(quality["axis_ratio"], 3))
    if quality["degenerate"]:
        problems.append(
            f"{acquisition['instrument']}: beam-profile fit is degenerate "
            f"({quality['reason']}); written as exported and flagged"
        )
        flag.attrs["description"] = quality["reason"]


def _write_analysis(
    group: h5py.Group,
    acquisition: Mapping,
    csv: Path | None,
    problems: list[str],
) -> None:
    """The vendor's Statistics and Peak Profile tools, typed."""
    blocks = _blocks(group, csv, problems)

    stats, stat_parameters, stat_problems = roi_statistics(blocks)
    problems.extend(stat_problems)
    if stats:
        _tool(
            group,
            "roi_statistics",
            "camera acquisition software (Statistics tool)",
            acquisition,
            (stats, stat_parameters),
        )

    fit, fit_parameters, fit_problems = beam_profile_fit(blocks)
    problems.extend(fit_problems)
    if fit:
        process = _tool(
            group,
            "beam_profile_fit",
            "camera acquisition software (Peak Profile tool)",
            acquisition,
            (fit, fit_parameters),
        )
        _flag_fit(process, fit, acquisition, problems)
        _h5.field(process, "notes", BEAM_FIT_NOTES)


def write(
    group: h5py.Group, acquisition: Mapping, read_path: Callable[[str], Path]
) -> list[str]:
    """Write one camera acquisition into the NXdetector ``group``."""
    problems: list[str] = []
    files = [Path(read_path(f)) for f in acquisition["files"]]
    png = next((f for f in files if is_measurement(PACK, f.name)), None)
    csv = next((f for f in files if f.suffix.lower() == ".csv"), None)
    # With no 16-bit frame, an opted-in instrument's BMP is the frame. Beside
    # an `_original.png` it is only a copy, and the PNG wins.
    bmp = None if png else bmp_frame(files)
    frame = png or bmp

    metadata: dict = {}
    if csv and csv.is_file():
        try:
            metadata = read_csv_metadata_file(csv)
        except (ValueError, OSError) as error:
            problems.append(f"unreadable CSV {csv.name}: {error}")
    elif bmp is None:
        problems.append(f"missing CSV {csv.name if csv else 'sidecar'}")

    shape = _write_frame(group, frame, problems)

    if bmp is not None and not metadata:
        _write_name_only(group, acquisition, bmp, shape)
        return problems

    typed = _write_sidecar(group, metadata, csv, problems)
    _write_base_class_fields(group, typed, shape)
    _write_analysis(group, acquisition, csv, problems)
    return problems
