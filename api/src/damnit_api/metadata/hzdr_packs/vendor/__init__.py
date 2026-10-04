"""shot-aligner's readers, pack helpers and pack manifests, vendored unchanged.

Every other file in this folder is a byte-for-byte copy of shot-aligner's,
pinned by sha256 in ``SOURCE.json`` and checked by
``hzdr/scripts/sync-hzdr-packs.{sh,ps1}`` (plan decision 1). shot-aligner owns
them until cutover: a fix goes there and is re-vendored with ``--apply``,
never made here. This ``__init__`` is DAMNIT's own and not vendored.

What DAMNIT uses from them:

``img_csv.read_csv_metadata_file``, ``irr8.read_irr8``
    the format readers (stdlib only). ``img_csv.read_image`` is OpenCV and is
    not used; ``hzdr_packs._images.read_image`` stands in for it.
``camera_metadata``
    typed camera settings and the vendor analysis block.
``nxwrite._safe``, ``nxwrite.set_scale_hint``
    NeXus-safe names and the silx scale hint. The display-copy and
    ``source_of`` helpers are not used: decision 6 keeps only each detector's
    default plot, and DAMNIT resolves recorded paths itself.
``<pack>.json``
    each pack's declarative manifest (which suffixes are the measurement,
    which scale a viewer is asked to use), read by :func:`manifest`.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

HERE = Path(__file__).resolve().parent


@cache
def manifest(pack_id: str) -> dict:
    """The vendored ``<pack_id>.json`` from shot-aligner's diagnostics folder."""
    return json.loads((HERE / f"{pack_id}.json").read_text(encoding="utf-8"))


def measurement_suffixes(pack_id: str) -> tuple[str, ...]:
    """The suffixes of the file that carries the measurement, as shot-aligner's.

    ``Pack.measurement_suffixes``: the manifest's ``measurementSuffixes``, or
    every claimed suffix when it names none.
    """
    claims = manifest(pack_id)["claims"]
    return tuple(claims.get("measurementSuffixes", claims.get("suffixes", ())))


def is_measurement(pack_id: str, name: str) -> bool:
    """``Pack.is_measurement``: whether a file name is the measurement half."""
    return name.endswith(measurement_suffixes(pack_id))


def signal_scale_type(pack_id: str) -> str | None:
    """``Pack.signal_scale_type``: the scale a viewer is asked to open it on."""
    return manifest(pack_id).get("nexus", {}).get("signalScaleType") or None


def bmp_frame_suffixes(pack_id: str) -> tuple[str, ...]:
    """``Pack.bmp_frame_suffixes``: BMP frames, in order of preference."""
    return tuple(manifest(pack_id)["claims"].get("bmpFrameSuffixes", ()))
