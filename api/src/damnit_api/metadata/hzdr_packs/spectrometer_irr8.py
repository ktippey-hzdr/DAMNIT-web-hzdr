"""Spectrometer exports: one ``.Irr8.txt`` per recording.

A port of shot-aligner's ``diagnostics/spectrometer_irr8.py`` ``write`` to
h5py, with the same output. The file is read whole through the vendored
``irr8.read_irr8``: an Irr8 file is a few thousand rows of text, so it is not
what bounds memory. The columns are separated from the header settings by
shape, not by name, so a firmware that adds or renames a column still lands in
the right place.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from . import _h5
from .vendor import is_measurement, signal_scale_type
from .vendor.irr8 import read_irr8
from .vendor.nxwrite import _safe, set_scale_hint

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import h5py

PACK = "spectrometer_irr8"


def _plot(group: h5py.Group, columns: dict, problems: list[str], instrument) -> None:
    """Irradiance against wavelength, as irr8.py itself links it."""
    signal = next(
        (name for name in ("absolute_irradiance", "sample") if name in columns), None
    )
    if not (signal and "wave" in columns):
        problems.append(
            f"{instrument}: no wavelength/irradiance pair to plot; the columns are "
            "stored raw"
        )
        return
    data = _h5.group(group, "data", "NXdata")
    raw = _h5.subgroup(group, "raw_data")
    for name in (signal, "wave"):
        _h5.link(data, name, _h5.dataset(raw, name))
    data.attrs["signal"] = signal
    data.attrs["axes"] = np.array(["wave"], dtype=_h5.TEXT)
    _h5.dataset(data, signal).attrs["interpretation"] = "spectrum"
    _h5.dataset(data, "wave").attrs["long_name"] = "wavelength"
    # The manifest's scale ("log": an emission line stands orders of
    # magnitude above its background).
    set_scale_hint(data, signal_scale_type(PACK))
    group.attrs["default"] = "data"


def write(
    group: h5py.Group, acquisition: Mapping, read_path: Callable[[str], Path]
) -> list[str]:
    """Write one spectrometer reading: the columns, their units, and a plot."""
    instrument = acquisition["instrument"]
    files = [Path(read_path(f)) for f in acquisition["files"]]
    source = next((f for f in files if is_measurement(PACK, f.name)), None)
    if source is None or not source.is_file():
        return [f"missing Irr8 file for {instrument}"]

    try:
        measurement = read_irr8(source)
    except Exception as error:  # any malformed file costs its detector, not the build
        return [f"{instrument}: could not read {source.name}: {error}"]

    columns = {
        key: value
        for key, value in measurement.items()
        if isinstance(value, dict) and "data" in value
    }
    settings = {
        key: value
        for key, value in measurement.items()
        if isinstance(value, dict) and "value" in value
    }
    if not columns:
        return [f"{instrument}: {source.name} holds no data columns"]

    problems: list[str] = []
    raw = _h5.group(group, "raw_data", "NXcollection")
    for key, entry in columns.items():
        units = (entry.get("units") or "").strip()
        _h5.field(
            raw, _safe(str(key)), np.asarray(entry["data"], dtype=float), units=units
        )

    _plot(group, columns, problems, instrument)

    collection = _h5.group(group, "metadata", "NXcollection")
    for key, entry in settings.items():
        units = (entry.get("units") or "").strip()
        # "[name]" is the header's way of saying "this is text", not a unit.
        _h5.field(
            collection,
            _safe(str(key)),
            entry["value"],
            units=units if units != "name" else None,
        )
    if not any(v.get("data") for v in columns.values()):
        problems.append(f"{instrument}: {source.name} has empty columns")

    fabrication = _h5.group(group, "fabrication", "NXfabrication")
    _h5.field(fabrication, "serial_number", source.name.split("_", maxsplit=1)[0])

    # NXdetector's standard name for the header's "Integration time", matched
    # by unit rather than by spelling.
    integration = next(
        (
            entry
            for key, entry in settings.items()
            if "integration" in str(key).lower()
            and (entry.get("units") or "").strip() in {"ms", "s", "us"}
        ),
        None,
    )
    if integration is not None:
        _h5.field(
            group,
            "count_time",
            integration["value"],
            units=(integration.get("units") or "").strip(),
            source_key="Integration time",
        )
    return problems
