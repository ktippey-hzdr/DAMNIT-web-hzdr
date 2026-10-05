"""The few h5py writes a pack needs, shaped like nexusformat's.

shot-aligner's packs write through ``nexusformat``; DAMNIT writes h5py
throughout and does not take that dependency (plan decision 2). These helpers
reproduce what each nexusformat call leaves in the file -- the ``NX_class``,
the dtype a Python value is stored as, the ``target`` attribute a hard link
carries, the ``date`` an ``NXnote`` is given -- so a pack reads like the
original and its output compares node for node with shot-aligner's.

Images are written chunked and gzip-4 compressed, and a stack of frames grows
one frame at a time (:class:`FrameStack`), so memory is bounded by a frame.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import h5py
import numpy as np

if TYPE_CHECKING:
    from collections.abc import Sequence

TEXT = h5py.string_dtype("utf-8")
GZIP_LEVEL = 4


def group(parent: h5py.Group, name: str, nx_class: str) -> h5py.Group:
    """``parent[name] = NX<class>()``: a group, created or reused, with its class."""
    created = parent.require_group(name)
    created.attrs["NX_class"] = nx_class
    return created


def text(value: str | bytes) -> str:
    """Text as nexusformat stores it (``tree.text``): NULs dropped, right-stripped.

    nexusformat applies this to every text field and attribute it writes, so a
    CRLF-terminated sidecar loses its last line end there; bytes are decoded as
    UTF-8 first.
    """
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return value.replace("\x00", "").rstrip()


def subgroup(parent: h5py.Group, name: str) -> h5py.Group:
    """``parent[name]``, which must be a group."""
    found = parent[name]
    if not isinstance(found, h5py.Group):
        msg = f"{parent.name}/{name} is not a group"
        raise TypeError(msg)
    return found


def dataset(parent: h5py.Group, name: str) -> h5py.Dataset:
    """``parent[name]``, which must be a dataset."""
    found = parent[name]
    if not isinstance(found, h5py.Dataset):
        msg = f"{parent.name}/{name} is not a dataset"
        raise TypeError(msg)
    return found


def field(
    parent: h5py.Group,
    name: str,
    value,
    units: str | None = None,
    **attrs,
) -> h5py.Dataset:
    """``parent[name] = NXfield(value, units=...)``, with the dtype nexusformat picks.

    Text is variable-length UTF-8, normalised by :func:`text` (as are text
    attributes), ``bool`` is ``bool``, ``int`` is int64 and
    ``float`` float64; an array keeps its own dtype.
    """
    if isinstance(value, (str, bytes)):
        created = parent.create_dataset(name, data=text(value), dtype=TEXT)
    elif isinstance(value, (bool, np.bool_)):
        created = parent.create_dataset(name, data=np.bool_(value))
    elif isinstance(value, (int, np.integer)):
        created = parent.create_dataset(name, data=np.int64(value))
    elif isinstance(value, (float, np.floating)):
        created = parent.create_dataset(name, data=np.float64(value))
    else:
        created = parent.create_dataset(name, data=np.asarray(value))
    if units:
        created.attrs["units"] = text(units)
    for key, given in attrs.items():
        created.attrs[key] = text(given) if isinstance(given, (str, bytes)) else given
    return created


def link(parent: h5py.Group, name: str, source: h5py.Dataset) -> h5py.Dataset:
    """``NXdata.makelink(source)``: a hard link, and ``@target`` naming the source.

    nexusformat marks a linked object with the path it was linked from, which
    is the NeXus convention for saying which name is the original.
    """
    parent[name] = source
    source.attrs["target"] = source.name
    return dataset(parent, name)


def note(parent: h5py.Group, name: str, **fields: str) -> h5py.Group:
    """``NXnote(**fields)``: text fields, and the ``date`` nexusformat stamps.

    The date is the time of writing, as nexusformat gives it; it says when the
    container was written, not when anything was measured.
    """
    created = group(parent, name, "NXnote")
    for key, text in fields.items():
        field(created, key, text)
    if "date" not in created:
        field(created, "date", datetime.now().astimezone().isoformat())
    return created


def image(parent: h5py.Group, name: str, frame: np.ndarray, **attrs) -> h5py.Dataset:
    """One frame as a chunked, gzip-4, shuffled dataset."""
    created = parent.create_dataset(
        name,
        data=frame,
        chunks=True,
        compression="gzip",
        compression_opts=GZIP_LEVEL,
        shuffle=True,
    )
    for key, given in attrs.items():
        created.attrs[key] = text(given) if isinstance(given, (str, bytes)) else given
    return created


class FrameStack:
    """``raw_data/image[frame, y, x]``, appended one frame at a time.

    Created on the first frame that reads, with that frame's shape and dtype,
    one frame per chunk and an unlimited first axis. Each position is either
    written with its frame or left as the fill value (zeros), so a frame
    number keeps meaning the same frame. Nothing but the frame being written
    is held: a 200-frame pco.edge recording is 1.6 GB, a date up to 20 GB.
    """

    def __init__(self, parent: h5py.Group, name: str) -> None:
        self.parent, self.name = parent, name
        self.dataset: h5py.Dataset | None = None
        self.length = 0

    @property
    def frame_shape(self) -> Sequence[int] | None:
        return None if self.dataset is None else self.dataset.shape[1:]

    def skip(self) -> None:
        """A position with no frame: zeros, flagged by the caller."""
        self.length += 1
        if self.dataset is not None:
            self.dataset.resize(self.length, axis=0)

    def append(self, frame: np.ndarray) -> None:
        if self.dataset is None:
            self.dataset = self.parent.create_dataset(
                self.name,
                shape=(self.length, *frame.shape),
                maxshape=(None, *frame.shape),
                chunks=(1, *frame.shape),
                dtype=frame.dtype,
                compression="gzip",
                compression_opts=GZIP_LEVEL,
            )
        self.length += 1
        self.dataset.resize(self.length, axis=0)
        self.dataset[self.length - 1] = frame
