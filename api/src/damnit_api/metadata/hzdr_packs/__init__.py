"""Diagnostic packs: one acquisition's files into one ``NXdetector``, with h5py.

Phase 2b of the campaign NeXus output plan (HZDR_combo
``planning/NEXUS_OUTPUT_PLAN.md``). shot-aligner's packs
(``shotalign/diagnostics/``) write into an in-memory ``nexusformat`` tree and
read frames with OpenCV. DAMNIT has neither, so:

* the **readers and helpers** are vendored unchanged into :mod:`.vendor`
  (decision 1; ``hzdr/scripts/sync-hzdr-packs.{sh,ps1}`` keeps them in step);
* the **packs** are rewritten here to h5py (decision 2), one module per pack
  id, each a port of shot-aligner's ``write`` with the same output: held node
  for node and value for value to shot-aligner's per-pack references
  (``tests/fixtures/hzdr-reference/packs/``);
* **frames stream**: each is read, written into a chunked gzip-4 dataset and
  released before the next is read, so memory is bounded by one frame however
  long the recording (a date is 1-20 GB).

The interface every pack shares::

    write(group, acquisition, read_path) -> problems

``group`` is the ``NXdetector`` to fill; the pack sets its ``default`` when it
wrote something plottable. ``acquisition`` is shot-aligner's acquisition dict:
``files`` (as recorded), ``instrument`` (for messages), and where a pack uses
them ``seq``, ``label`` and ``when``. ``read_path`` turns one recorded file
into the local path to read, which is where DAMNIT's path map applies.
``problems`` says what could not be written; a missing or unreadable file is a
problem, never an exception, so one bad file costs its detector and not the
build.

Phase 3 calls them from :mod:`..hzdr_containers` (design:
``hzdr/docs/plans/container-writer.md``), which supplies what shot-aligner's
alignment index supplied:

* ``seq`` and ``label`` from the file names, with the vendored manifests'
  ``namePatterns`` (shot-aligner's ``claim``); events carry neither;
* ``when`` from ``metadata.acquisition.time``;
* the grouping of a recording's per-frame events into one acquisition: files
  with the same claim key (a recording's label, a frame's stem) are one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import camera_png_csv, sequence_frames, spectrometer_irr8

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    import h5py

    ReadPath = Callable[[str], Path]

PACKS = {
    "camera_png_csv": camera_png_csv,
    "spectrometer_irr8": spectrometer_irr8,
    "sequence_frames": sequence_frames,
}


def write(
    pack_id: str, group: h5py.Group, acquisition: Mapping, read_path: ReadPath
) -> list[str]:
    """Write one acquisition with the pack ``pack_id`` (``instrument.format``)."""
    pack = PACKS.get(pack_id)
    if pack is None:
        msg = f"no pack {pack_id!r}; known: {', '.join(sorted(PACKS))}"
        raise KeyError(msg)
    return pack.write(group, acquisition, read_path)


__all__ = ["PACKS", "write"]
