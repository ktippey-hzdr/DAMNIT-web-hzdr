"""Shot containers: one NeXus file per shot, converted from its events' raws.

Phase 3 of the campaign NeXus output plan (HZDR_combo
``planning/NEXUS_OUTPUT_PLAN.md``); the design and its reasons are in
``hzdr/docs/plans/container-writer.md``. In short:

* the **published master** says which events belong to which shot: the
  converter reads ``/entry/source_events`` (joined by ``event_id``; the bridge
  profile is unchanged) and ``/entry/shots``, then closes it;
* per shot and instrument, files are grouped into **acquisitions** by the key
  shot-aligner's ``claim()`` gives their names (:func:`claim`), and each is
  written by its pack (``metadata.instrument.format``, :mod:`.hzdr_packs`)
  into ``/entry/<NXinstrument>/<NXdetector>``, named as shot-aligner names it
  (``nxwrite.container_groups`` fed from the vendored NDS catalogue);
* each container is written to ``<name>.nxs.<nonce>.tmp`` (the writer's own)
  and renamed into place, and
  is rewritten only when its **input fingerprint** changes
  (``shots/.build-manifest.json``, and the container's own attribute);
* :func:`run_conversion` holds a **conversion lock** per campaign
  (``shots/.convert.lock``), never the master's, so the builder publishes
  while a date converts; ``api/scripts/hzdr-container-worker.py`` runs it.

A missing or unreadable file never fails the run: its detector is left out and
the reason recorded in ``/entry/conversion_problems``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import h5py
import PIL

from ..shared.hzdr_paths import map_path, parse_path_map
from . import hzdr_packs
from .hzdr_nexus import (
    BuilderAlreadyRunningError,
    LockLostError,
    replace_with_retry,
    single_writer_lock,
    write_json_atomic,
)
from .hzdr_packs import _h5, vendor
from .hzdr_packs.vendor.nxwrite import _safe, container_groups

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    ReadPath = Callable[[str], Path]

logger = logging.getLogger(__name__)

CONTAINER_PROFILE = "hzdr-shot-container-v1"
SHOTS_DIRNAME = "shots"
MANIFEST_NAME = ".build-manifest.json"
PENDING_NAME = ".convert.pending"
# single_writer_lock(<shots>/.convert) holds <shots>/.convert.lock.
_LOCK_STEM = ".convert"
_TMP_SUFFIX = ".tmp"
# Rows of /entry/source_events read at a time: memory is bounded by a slice of
# JSON text, not by the campaign's event count.
READ_SLICE = 4096
# A worker's conversion lock not refreshed for this long is a dead worker's
# (a host that rebooted, a PID reused); it is refreshed once per container.
LOCK_STALE_AFTER_S = 30 * 60
# Names the writer gives entry-level fields and NXinstrument fields; a group
# that sanitises to one of them gets a suffix instead of overwriting it.
RESERVED_ENTRY_NAMES = frozenset({
    "title",
    "start_time",
    "end_time",
    "experiment_identifier",
    "entry_identifier",
    "definition",
    "data",
    "labfrog_shot",
    "conversion_problems",
})
RESERVED_INSTRUMENT_NAMES = frozenset({"name"})

_SHOT_KEY = re.compile(r"^(?P<campaign>.+):(?P<date>\d{8}|unknown):(?P<number>\d{6,})$")

TIMING_ROLE_DESCRIPTIONS = {
    "pre_shot": "recorded before the laser event; this stamp precedes the "
    "shot and is not the shot time",
    "on_shot": "recorded from the laser event itself",
    "post_shot": "recorded after the laser event",
}
OFFSET_DESCRIPTION = (
    "DAMNIT fits no recording offset: the time above is the producer's "
    "(metadata.acquisition.time), attributed to this shot by the producer and "
    "DAMNIT's matching, not by a fitted clock"
)


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def container_name(shot_key: str) -> str:
    """``<YYYYMMDD>_<shot_number:06d>.nxs`` from ``campaign:YYYYMMDD:NNNNNN``.

    Not the ``shot_key`` itself: its colons are illegal on Windows and on the
    ``Z:`` share, and its campaign part changes when a ruling moves the shot.
    """
    match = _SHOT_KEY.match(shot_key)
    if match is None:
        msg = f"not a shot_key (campaign:YYYYMMDD:NNNNNN): {shot_key!r}"
        raise ValueError(msg)
    return f"{match['date']}_{int(match['number']):06d}.nxs"


def shots_dir(master: Path) -> Path:
    """The campaign's container folder, beside its master."""
    return master.parent / SHOTS_DIRNAME


class PathMapReader:
    """Recorded path -> this host's path, through ``DW_API_METADATA__PATH_MAP``.

    ``digest`` names the map, so a container read through another one is
    rewritten (it is part of the fingerprint).
    """

    def __init__(self, path_map: str) -> None:
        self.rules = parse_path_map(path_map)
        self.digest = hashlib.sha256(path_map.strip().encode()).hexdigest()

    def __call__(self, recorded: str) -> Path:
        return map_path(recorded, self.rules) or Path(recorded)


def make_read_path(path_map: str) -> PathMapReader:
    return PathMapReader(path_map)


def _recorded_name(recorded: str) -> str:
    """A recorded path's file name, whichever separator it was written with."""
    return recorded.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# Acquisition keys: shot-aligner's claim(), from the vendored name patterns
# ---------------------------------------------------------------------------


def _stem(pack_id: str, name: str) -> str:
    """The name without its claimed suffix, as each pack's ``_stem`` strips it."""
    claims = vendor.manifest(pack_id)["claims"]
    suffixes = list(claims.get("suffixes", ()))
    if pack_id == "sequence_frames":
        sidecars = tuple(claims.get("sidecarSuffixes", ()))
        for sidecar in sidecars:
            name = name.removesuffix(sidecar)
        suffixes = [s for s in suffixes if s not in sidecars]
    for suffix in sorted(suffixes, key=len, reverse=True):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    lowered = name.lower()
    for suffix in sorted(vendor.bmp_frame_suffixes(pack_id), key=len, reverse=True):
        if lowered.endswith(suffix.lower()):
            return name[: -len(suffix)]
    return name


def claim(
    pack_id: str, name: str, folder_label: str = ""
) -> tuple[str, str, int | None]:
    """``(key, label, seq)`` for one file name, as shot-aligner's ``claim()``.

    Files with the same key are one acquisition: a camera frame and its CSV
    (one stem), every frame of a recording named by its conditions (the label),
    a frame and its ``.rec``. shot-aligner's recording claim also wants a
    quantity token and refuses a set label, because there it decides whether a
    file is an acquisition at all; a producer has decided that here, so a name
    no pattern fits is kept, keyed by its stem, without ``seq``.
    """
    stem = _stem(pack_id, name)
    for entry in vendor.manifest(pack_id).get("namePatterns", []):
        match = re.match(entry["regex"], stem)
        if match is None:
            continue
        provides = entry.get("provides", "full")
        groups = match.groupdict()
        if pack_id == "spectrometer_irr8":
            # The stamped dialect's ordinal is always 0001 and is discarded.
            seq = int(groups["seq"]) if provides == "sequence" else None
            return f"{folder_label}|{stem}", folder_label, seq
        if provides == "recording":
            return groups["label"], groups["label"], None
        seq = groups.get("seq")
        return stem, groups.get("label") or "", int(seq) if seq else None
    if pack_id == "spectrometer_irr8":
        return f"{folder_label}|{stem}", folder_label, None
    return stem, "", None


# ---------------------------------------------------------------------------
# What a shot holds: read from the published master
# ---------------------------------------------------------------------------


@dataclass
class Acquisition:
    """One pack call: one instrument's files of one acquisition in one shot."""

    instrument_id: str
    pack: str
    instrument: str  # the label (catalogue display_name, folder name)
    layout: dict[str, str]
    key: str
    label: str = ""
    seq: int | None = None
    files: list[str] = field(default_factory=list)
    sha256: dict[str, str | None] = field(default_factory=dict)
    when: str = ""
    time_source: str = ""
    timing_role: str = ""

    def facts(self) -> dict[str, Any]:
        """Everything the container is written from, for the fingerprint."""
        return {
            "instrument_id": self.instrument_id,
            "pack": self.pack,
            "instrument": self.instrument,
            "layout": self.layout,
            "key": self.key,
            "label": self.label,
            "seq": self.seq,
            "files": self.files,
            "when": self.when,
            "time_source": self.time_source,
            "timing_role": self.timing_role,
        }


@dataclass
class ShotPlan:
    """One container's inputs."""

    shot_key: str
    experiment_id: str
    fired_at: str = ""
    labfrog: dict[str, Any] = field(default_factory=dict)
    acquisitions: list[Acquisition] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return container_name(self.shot_key)

    @property
    def date(self) -> str:
        day = self.shot_key.rsplit(":", 2)[1]
        return day if day == "unknown" else f"{day[:4]}-{day[4:6]}-{day[6:]}"

    @property
    def number(self) -> int:
        return int(self.shot_key.rsplit(":", 1)[1])


def _column(group: h5py.Group | None, name: str, count: int) -> list[Any]:
    if group is None or name not in group:
        return [None] * count
    dataset = group[name]
    if not isinstance(dataset, h5py.Dataset):
        return [None] * count
    if dataset.dtype.kind in "SOU":
        return list(dataset.asstr()[()])
    return [value.item() if hasattr(value, "item") else value for value in dataset[()]]


def _group(handle: h5py.File, path: str) -> h5py.Group | None:
    found = handle.get(path)
    return found if isinstance(found, h5py.Group) else None


def read_master(master: Path) -> tuple[str, list[dict], list[dict]]:
    """``(experiment_id, shot rows, acquisition event rows)`` of a published master.

    Opened read-only, read in slices of ``READ_SLICE`` rows and closed before
    anything is converted: only events naming ``metadata.instrument.format``
    are kept, and of them only what a container is written from, so memory
    follows the acquisitions, not the campaign's event table, and the master is
    held as briefly as the tables allow (on Windows an open handle makes the
    builder's rename wait; it retries).
    """
    with h5py.File(master, "r") as handle:
        experiment_id = handle.attrs.get("experiment_id", master.stem)
        if isinstance(experiment_id, bytes):
            experiment_id = experiment_id.decode()
        shots = _rows(
            _group(handle, "entry/shots"),
            (
                "shot_key",
                "fired_at",
                "record_id",
                "labfrog_date_time",
                "labfrog_local_count",
            ),
        )
        events = _acquisition_events(_group(handle, "entry/source_events"))
    return str(experiment_id), shots, events


def _rows(group: h5py.Group | None, names: Iterable[str]) -> list[dict]:
    if group is None or "shot_key" not in group:
        return []
    key = group["shot_key"]
    count = len(key) if isinstance(key, h5py.Dataset) else 0
    columns = {name: _column(group, name, count) for name in names}
    return [{name: values[i] for name, values in columns.items()} for i in range(count)]


def _text_slice(group: h5py.Group, name: str, start: int, stop: int) -> list[str]:
    dataset = group.get(name)
    if not isinstance(dataset, h5py.Dataset):
        return [""] * (stop - start)
    return [str(value) for value in dataset.asstr()[start:stop]]


def _compact_payload(payload: dict) -> dict:
    """The payload fields a container uses: paths and their sha256."""
    keep = ("path", "sha256", "size_bytes")
    members = [
        {k: m[k] for k in keep if k in m}
        for m in payload.get("members") or []
        if isinstance(m, dict)
    ]
    compact = {k: payload[k] for k in keep if k in payload}
    if members:
        compact["members"] = members
    return compact


def _compact_metadata(metadata: dict) -> dict:
    """The metadata a container uses: instrument, watch folder, acquisition time."""
    compact = {"instrument": metadata["instrument"]}
    watch = _as_object(metadata.get("watch"))
    if watch:
        compact["watch"] = {
            k: watch[k] for k in ("watch_name", "watch_path") if k in watch
        }
    acquisition = _as_object(metadata.get("acquisition"))
    if acquisition:
        compact["acquisition"] = {
            k: acquisition[k] for k in ("time", "time_source") if k in acquisition
        }
    return compact


def _acquisition_events(group: h5py.Group | None) -> list[dict]:
    if group is None or "shot_key" not in group or "metadata_json" not in group:
        return []
    keys_dataset = group["shot_key"]
    count = len(keys_dataset) if isinstance(keys_dataset, h5py.Dataset) else 0
    kept: list[dict] = []
    for start in range(0, count, READ_SLICE):
        stop = min(start + READ_SLICE, count)
        keys = _text_slice(group, "shot_key", start, stop)
        texts = _text_slice(group, "metadata_json", start, stop)
        rows = []
        for i, (key, text) in enumerate(zip(keys, texts, strict=True)):
            if not key or '"format"' not in text:
                continue
            metadata = _json_object(text)
            instrument = metadata.get("instrument")
            if isinstance(instrument, dict) and instrument.get("format"):
                rows.append((i, key, _compact_metadata(metadata)))
        if not rows:
            continue
        ids = _text_slice(group, "event_id", start, stop)
        payloads = _text_slice(group, "payload_ref_json", start, stop)
        kept += [
            {
                "event_id": ids[i],
                "shot_key": key,
                "metadata": metadata,
                "payload_ref": _compact_payload(_json_object(payloads[i])),
            }
            for i, key, metadata in rows
        ]
    return kept


def _as_object(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _json_object(text: Any) -> dict:
    try:
        return _as_object(json.loads(text) if text else {})
    except (TypeError, ValueError):
        return {}


def _layout(instrument_id: str, label: str) -> tuple[str, dict[str, str], bool]:
    """``(label, container_groups entry, catalogued)`` for one instrument."""
    entry = vendor.catalogue().get(instrument_id)
    if entry is None:
        return label, {"instrumentName": label}, False
    layout = {
        "group": entry.get("family") or "",
        "instrumentName": entry.get("instrument_name")
        or entry.get("display_name")
        or "",
        "detectorName": entry.get("detector_name") or "",
    }
    return (
        entry.get("display_name") or label,
        {k: v for k, v in layout.items() if v},
        True,
    )


def _folder_label(recorded: str, watch_path: str) -> str:
    """The folder between the watched one and the file, as shot-aligner's scan."""
    parent = recorded.replace("\\", "/").rsplit("/", 1)[0]
    watched = (watch_path or "").replace("\\", "/").rstrip("/")
    if not watched or parent == watched:
        return ""
    return parent.rsplit("/", 1)[-1]


def _plans_from_shot_rows(experiment_id: str, shots: list[dict]) -> dict[str, ShotPlan]:
    by_key: dict[str, ShotPlan] = {}
    for row in shots:
        if not row.get("shot_key"):
            continue
        labfrog = {}
        if row.get("record_id") or row.get("labfrog_date_time"):
            labfrog = {
                "record_id": row.get("record_id") or "",
                "date_time": row.get("labfrog_date_time") or "",
                "local_count": row.get("labfrog_local_count"),
            }
        by_key[row["shot_key"]] = ShotPlan(
            shot_key=row["shot_key"],
            experiment_id=experiment_id,
            fired_at=row.get("fired_at") or "",
            labfrog=labfrog,
        )
    return by_key


def _instrument_of(row: dict, plan: ShotPlan, metadata: dict) -> tuple | None:
    """``(instrument_id, pack, label, layout)`` for an event, or None (a problem)."""
    instrument = metadata["instrument"]
    watch = _as_object(metadata.get("watch"))
    instrument_id = str(
        instrument.get("id") or watch.get("watch_name") or row.get("event_id") or ""
    )
    pack = str(instrument["format"])
    if pack not in hzdr_packs.PACKS:
        plan.problems.append(
            f"{instrument_id}: no pack writes {pack!r}; nothing was written for "
            f"event {row.get('event_id')}"
        )
        return None
    label, layout, catalogued = _layout(
        instrument_id, str(watch.get("watch_name") or instrument_id)
    )
    message = (
        f"{instrument_id} is not in the instrument catalogue; written under "
        f"its own name ({label}) with no family"
    )
    if not catalogued and message not in plan.problems:
        plan.problems.append(message)
    return instrument_id, pack, label, layout


def _add_event(
    row: dict,
    plan: ShotPlan,
    metadata: dict,
    acquisitions: dict[tuple[str, str, str], Acquisition],
) -> None:
    """Put one event's files into the acquisitions their names claim."""
    found = _instrument_of(row, plan, metadata)
    if found is None:
        return
    instrument_id, pack, label, layout = found
    watch_path = str(_as_object(metadata.get("watch")).get("watch_path") or "")
    acquired = _as_object(metadata.get("acquisition"))
    when = str(acquired.get("time") or "")
    payload = _as_object(row.get("payload_ref")) or _json_object(
        row.get("payload_ref_json")
    )
    members = payload.get("members") or ([payload] if payload.get("path") else [])
    for member in members:
        recorded = member.get("path") if isinstance(member, dict) else None
        if not recorded:
            continue
        key, name_label, seq = claim(
            pack, _recorded_name(recorded), _folder_label(recorded, watch_path)
        )
        acquisition = acquisitions.setdefault(
            (plan.shot_key, instrument_id, key),
            Acquisition(
                instrument_id=instrument_id,
                pack=pack,
                instrument=label,
                layout=layout,
                key=key,
                label=name_label,
                seq=seq,
                timing_role=str(
                    metadata["instrument"].get("timing_role")
                    or vendor.catalogue().get(instrument_id, {}).get("timing_role")
                    or ""
                ),
            ),
        )
        if recorded not in acquisition.sha256:
            acquisition.files.append(recorded)
            acquisition.sha256[recorded] = member.get("sha256")
        if when and (not acquisition.when or when < acquisition.when):
            acquisition.when = when
            acquisition.time_source = str(acquired.get("time_source") or "")


def plan_shots(
    experiment_id: str, shots: list[dict], events: list[dict]
) -> list[ShotPlan]:
    """Group a master's events into containers and acquisitions.

    Only events whose ``metadata.instrument.format`` is set are acquisitions;
    triggers and LabFrog rows are not, and a shot with none gets no container.
    """
    by_key = _plans_from_shot_rows(experiment_id, shots)
    acquisitions: dict[tuple[str, str, str], Acquisition] = {}
    with_files: set[str] = set()
    for row in events:
        shot_key = row.get("shot_key") or ""
        metadata = _as_object(row.get("metadata")) or _json_object(
            row.get("metadata_json")
        )
        instrument = metadata.get("instrument")
        if not shot_key or not isinstance(instrument, dict):
            continue
        if not instrument.get("format"):
            continue
        plan = by_key.setdefault(shot_key, ShotPlan(shot_key, experiment_id))
        with_files.add(shot_key)
        _add_event(row, plan, metadata, acquisitions)
    # Deterministic whatever order the events arrived in: by instrument, then
    # time, then key, so the earliest acquisition keeps the plain detector name.
    for (shot_key, _, _), acquisition in sorted(
        acquisitions.items(),
        key=lambda item: (
            item[0][0],
            item[1].instrument_id,
            item[1].when or "\uffff",
            item[1].key,
        ),
    ):
        acquisition.files.sort()
        by_key[shot_key].acquisitions.append(acquisition)
    return [by_key[key] for key in sorted(with_files)]


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------


def _source_files() -> list[Path]:
    packs = Path(hzdr_packs.__file__).resolve().parent
    found = [
        p
        for p in packs.rglob("*")
        if p.is_file() and p.suffix in {".py", ".json"} and "__pycache__" not in p.parts
    ]
    return [*sorted(found), Path(__file__).resolve()]


_CODE_DIGEST: str | None = None


def code_digest() -> str:
    """A digest of the conversion code: the packs, the vendored files, this module.

    Any change rebuilds every container, which the plan requires of a change
    that alters output (and accepts for one that does not).
    """
    global _CODE_DIGEST
    if _CODE_DIGEST is None:
        digest = hashlib.sha256()
        packs = Path(hzdr_packs.__file__).resolve().parent
        for path in _source_files():
            name = path.name if path.parent != packs else f"hzdr_packs/{path.name}"
            digest.update(name.encode() + b"\0" + path.read_bytes() + b"\0")
        _CODE_DIGEST = digest.hexdigest()
    return _CODE_DIGEST


def catalogue_sha256() -> str:
    return hashlib.sha256(
        (vendor.HERE / vendor.CATALOGUE_FILE).read_bytes()
    ).hexdigest()


def library_versions() -> dict[str, str]:
    """The libraries whose output a container holds: h5py, libhdf5, Pillow."""
    return {
        "h5py": h5py.__version__,
        "hdf5": h5py.version.hdf5_version,
        "pillow": PIL.__version__,
    }


Stat = list[int] | None  # [size, mtime_ns], or None for a file not there


def member_stats(plan: ShotPlan, read_path: ReadPath) -> dict[str, Stat]:
    """``[size, mtime_ns]`` of every member as this host sees it (None: missing).

    One stat per file per pass: it says whether a file is there (part of the
    fingerprint, so a file that comes back is converted even when the manifest
    was lost) and whether it changed under an unchanged sha256.
    """
    stats: dict[str, Stat] = {}
    for acquisition in plan.acquisitions:
        for recorded in acquisition.files:
            try:
                stat = read_path(recorded).stat()
            except OSError:
                stats[recorded] = None
            else:
                stats[recorded] = [stat.st_size, stat.st_mtime_ns]
    return stats


def _file_identity(sha256: str | None, stat: Stat) -> Any:
    """The producer's sha256 and whether the file is there; a stat without one."""
    if sha256:
        return [sha256, stat is not None]
    return stat if stat is not None else "missing"


def fingerprint(
    plan: ShotPlan, read_path: ReadPath, stats: Mapping[str, Stat] | None = None
) -> str:
    """The inputs a container is written from, as one sha256."""
    if stats is None:
        stats = member_stats(plan, read_path)
    record = {
        "profile": CONTAINER_PROFILE,
        "code": code_digest(),
        "catalogue": catalogue_sha256(),
        "libraries": library_versions(),
        "path_map": getattr(read_path, "digest", ""),
        "shot": {
            "shot_key": plan.shot_key,
            "experiment_id": plan.experiment_id,
            "fired_at": plan.fired_at,
            "labfrog": plan.labfrog,
            "problems": plan.problems,
        },
        "acquisitions": [
            {
                **a.facts(),
                "inputs": [
                    _file_identity(a.sha256.get(f), stats.get(f)) for f in a.files
                ],
            }
            for a in plan.acquisitions
        ],
    }
    text = json.dumps(record, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Writing one container
# ---------------------------------------------------------------------------


@dataclass
class ShotResult:
    problems: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    detectors: int = 0
    omitted: int = 0


def _keeps(detector: h5py.Group) -> bool:
    """Whether a pack wrote a measurement: a plot, or at least an array."""
    if "default" in detector.attrs:
        return True
    arrays: list[str] = []
    detector.visititems(
        lambda name, obj: (
            arrays.append(name)
            if isinstance(obj, h5py.Dataset) and obj.ndim > 0
            else None
        )
    )
    return bool(arrays)


def _problem(instrument: str, text: str) -> str:
    return text if text.startswith(instrument) else f"{instrument}: {text}"


def _unreserved(name: str, reserved: frozenset[str], suffix: str) -> str:
    return f"{name}_{suffix}" if name in reserved else name


def _detector_group(instrument: h5py.Group, name: str, acquisition: Acquisition) -> str:
    """The detector's group name under its NXinstrument.

    The first acquisition (earliest; see :func:`plan_shots`) keeps the plain
    name; a second takes shot-aligner's ``<detector>_<seq>``, or, with no
    ordinal, its sanitised key (the file stem or recording label), not ``_0``.
    """
    name = _unreserved(name, RESERVED_INSTRUMENT_NAMES, "detector")
    if name not in instrument:
        return name
    tail = (
        str(acquisition.seq)
        if acquisition.seq is not None
        else _safe(acquisition.key.rsplit("|", 1)[-1])
    )
    candidate, n = f"{name}_{tail}", 2
    while candidate in instrument or candidate in RESERVED_INSTRUMENT_NAMES:
        candidate, n = f"{name}_{tail}_{n}", n + 1
    return candidate


def _write_file_metadata(detector: h5py.Group, acquisition: Acquisition) -> None:
    """``compose_shot``'s ``file_metadata``: which file, from where, on what clock."""
    files = acquisition.files
    primary = next(
        (
            f
            for f in files
            if vendor.is_measurement(acquisition.pack, _recorded_name(f))
        ),
        files[0] if files else "",
    )
    notes = _h5.group(detector, "file_metadata", "NXnote")
    _h5.field(notes, "file_name", _recorded_name(primary))
    _h5.field(notes, "file_path", primary)
    _h5.field(
        notes,
        "sidecar_files",
        "\n".join(_recorded_name(f) for f in files if f != primary),
    )
    _h5.field(
        notes,
        "sha256",
        "\n".join(
            f"{_recorded_name(f)} {acquisition.sha256.get(f) or ''}".rstrip()
            for f in files
        ),
        description="each file's sha256 as its event recorded it, one per line",
    )
    _h5.field(notes, "file_creation_date", acquisition.when)
    _h5.field(notes, "time_source", acquisition.time_source)
    _h5.field(
        notes, "recorded_offset_removed", 0.0, units="s", description=OFFSET_DESCRIPTION
    )


def _write_acquisition(
    entry: h5py.Group, acquisition: Acquisition, read_path: ReadPath, result: ShotResult
) -> None:
    label = acquisition.instrument
    missing = [f for f in acquisition.files if not read_path(f).is_file()]
    for recorded in missing:
        result.problems.append(f"{label}: missing file {recorded}")
    result.missing += missing

    machine, detector_name = container_groups(acquisition.layout, label)
    machine = _unreserved(machine, RESERVED_ENTRY_NAMES, "instrument")
    created = machine not in entry
    instrument = _h5.group(entry, machine, "NXinstrument")
    if created:
        layout = acquisition.layout
        _h5.field(
            instrument,
            "name",
            layout.get("group") or layout.get("instrumentName") or label,
        )
    name = _detector_group(instrument, detector_name, acquisition)
    detector = _h5.group(instrument, name, "NXdetector")
    _h5.field(detector, "local_name", label)
    if acquisition.layout.get("detectorName"):
        detector.attrs["detector_name"] = acquisition.layout["detectorName"]

    pack_input = {
        "files": acquisition.files,
        "instrument": label,
        "seq": acquisition.seq,
        "label": acquisition.label,
        "when": acquisition.when,
    }
    try:
        found = hzdr_packs.write(acquisition.pack, detector, pack_input, read_path)
    except Exception as error:
        found = [f"conversion failed: {type(error).__name__}: {error}"]
    result.problems += [_problem(label, text) for text in found]

    if not _keeps(detector):
        del instrument[name]
        if set(instrument) <= {"name"}:
            del entry[machine]
        result.omitted += 1
        result.problems.append(
            f"{label}: detector omitted; nothing readable was written for it"
        )
        return
    result.detectors += 1
    role = acquisition.timing_role
    _h5.field(
        detector,
        "timing_role",
        role,
        description=TIMING_ROLE_DESCRIPTIONS.get(
            role, "timing relative to the laser event is undeclared"
        ),
    )
    _write_file_metadata(detector, acquisition)


def _write_shot(
    handle: h5py.File,
    plan: ShotPlan,
    read_path: ReadPath,
    fingerprint_text: str,
    notes: list[str],
) -> ShotResult:
    handle.attrs["NX_class"] = "NXroot"
    handle.attrs["default"] = "entry"
    handle.attrs["file_name"] = plan.name
    handle.attrs["creator"] = "DAMNIT-web-hzdr hzdr_containers"
    handle.attrs["damnit_container_profile"] = CONTAINER_PROFILE
    handle.attrs["damnit_input_fingerprint"] = fingerprint_text
    handle.attrs["shot_key"] = plan.shot_key
    handle.attrs["damnit_libraries"] = json.dumps(library_versions(), sort_keys=True)

    entry = _h5.group(handle, "entry", "NXentry")
    entry.attrs["shot_key"] = plan.shot_key
    entry.attrs["shot_number"] = plan.number
    entry.attrs["experiment_id"] = plan.experiment_id
    _h5.field(entry, "title", f"{plan.experiment_id} {plan.date} shot {plan.number}")
    if plan.fired_at:
        _h5.field(
            entry,
            "start_time",
            plan.fired_at,
            description="the shot's fired_at in the master's /entry/shots: the "
            "trigger time, or the earliest event time where there is no trigger",
        )
    _h5.field(entry, "experiment_identifier", plan.experiment_id)
    _h5.field(entry, "entry_identifier", plan.name.removesuffix(".nxs"))
    if plan.labfrog:
        labfrog = _h5.group(entry, "labfrog_shot", "NXcollection")
        labfrog.attrs["description"] = "the shot's LabFrog row, as the master holds it"
        _h5.field(labfrog, "record_id", plan.labfrog.get("record_id") or "")
        _h5.field(labfrog, "date_time", plan.labfrog.get("date_time") or "")
        local_count = plan.labfrog.get("local_count")
        if local_count is not None and int(local_count) >= 0:
            _h5.field(
                labfrog,
                "local_count",
                int(local_count),
                description="LabFrog's local Count, a user aid; never the shot number",
            )

    result = ShotResult(problems=[*plan.problems, *notes])
    for acquisition in plan.acquisitions:
        _write_acquisition(entry, acquisition, read_path, result)
    if result.problems:
        _h5.note(
            entry,
            "conversion_problems",
            type="text/plain",
            data="\n".join(result.problems),
            description="what could not be converted for this shot, one line each; "
            "a detector with nothing readable is left out",
        )
    return result


def write_container(
    target: Path,
    plan: ShotPlan,
    read_path: ReadPath,
    fingerprint_text: str,
    *,
    stats: Mapping[str, Stat] | None = None,
    notes: list[str] | None = None,
    nonce: str = "",
    before_publish: Callable[[], None] | None = None,
) -> ShotResult:
    """Write ``target`` whole: to ``<name>.<nonce>.tmp``, fsynced, then renamed.

    ``stats`` (the members' size and mtime now) are stored beside the
    fingerprint, so a later pass can tell a file changed under the same
    sha256; ``notes`` are problems the caller found (such a change).
    ``nonce`` (the lock's nonce) makes the temp name this writer's own, and
    ``before_publish`` (the lock's ``refresh``) runs just before the rename,
    so a writer whose lock was taken over stops instead of publishing.
    """
    temp = target.with_name(
        f"{target.name}.{nonce}{_TMP_SUFFIX}" if nonce else target.name + _TMP_SUFFIX
    )
    try:
        with h5py.File(temp, "w") as handle:
            handle.attrs["damnit_input_stats"] = json.dumps(stats or {}, sort_keys=True)
            result = _write_shot(handle, plan, read_path, fingerprint_text, notes or [])
        _fsync(temp)
        if before_publish is not None:
            before_publish()
        replace_with_retry(temp, target)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return result


def _fsync(path: Path) -> None:
    """Flush a written file to disk before it is renamed into place."""
    fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# One campaign
# ---------------------------------------------------------------------------


@dataclass
class ConversionSummary:
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    problems: int = 0


def _load_manifest(path: Path) -> dict:
    try:
        loaded = _as_object(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        loaded = {}
    return {
        "profile": CONTAINER_PROFILE,
        "containers": _as_object(loaded.get("containers")),
    }


def _stored(path: Path) -> tuple[str | None, dict | None]:
    """A container's own fingerprint and member stats, from its attributes."""
    try:
        with h5py.File(path, "r") as handle:
            wanted = handle.attrs.get("damnit_input_fingerprint")
            stats = handle.attrs.get("damnit_input_stats")
    except OSError:
        return None, None
    if isinstance(wanted, bytes):
        wanted = wanted.decode()
    try:
        parsed = json.loads(stats) if stats is not None else None
    except (TypeError, ValueError):
        parsed = None
    return wanted, parsed if isinstance(parsed, dict) else None


def _changed_files(
    target: Path, record: Mapping | None, wanted: str, stats: Mapping[str, Stat]
) -> list[str] | None:
    """None when ``target`` must be (re)written; else the files changed on disk.

    The manifest answers without opening the file; a container the manifest
    does not know (a run that died before flushing it) is asked itself. An
    empty list means up to date. A file whose size or mtime moved since the
    last conversion, under the same sha256, is listed: the caller reconverts
    it and says so.
    """
    if not target.is_file():
        return None
    if (
        record
        and record.get("fingerprint") == wanted
        and record.get("bytes") == target.stat().st_size
    ):
        before = record.get("stats")
    else:
        stored, before = _stored(target)
        if stored != wanted:
            return None
    if not isinstance(before, dict):
        return []
    return [
        recorded
        for recorded, now in stats.items()
        if now is not None and before.get(recorded) not in (None, now)
    ]


def convert_campaign(
    master: Path,
    *,
    read_path: ReadPath,
    flush_seconds: float = 5.0,
    after_write: Callable[[str], None] | None = None,
    heartbeat: Callable[[], None] | None = None,
    nonce: str = "",
) -> ConversionSummary:
    """Bring a campaign's ``shots/`` up to date with its published master.

    Call it under the conversion lock (:func:`run_conversion` does, and passes
    ``heartbeat`` to refresh it once per container, before each rename and
    before each manifest write; it raises :class:`LockLostError` when the lock
    was taken over, which ends the pass). Temp files of earlier writers are
    removed first: they hold no lock now, and one still running cannot publish
    because its own ``heartbeat`` fails. The manifest is flushed every
    ``flush_seconds`` and at the end. A container that cannot be written (a
    refused rename, a full disk) is recorded with its error and the pass goes
    on with the next shot.
    ``after_write(name)`` runs after each container is in place (for tests).
    """
    folder = shots_dir(master)
    folder.mkdir(parents=True, exist_ok=True)
    _clear_earlier_temps(folder, nonce)
    manifest_path = folder / MANIFEST_NAME
    manifest = _load_manifest(manifest_path)
    manifest["master"] = master.name
    records: dict = manifest["containers"]

    experiment_id, shots, events = read_master(master)
    summary = ConversionSummary()
    flushed, dirty = time.monotonic(), False
    for plan in plan_shots(experiment_id, shots, events):
        _checked(heartbeat)
        name = plan.name
        target = folder / name
        try:
            written = _convert_one(
                plan,
                target,
                records.get(name),
                read_path,
                nonce=nonce,
                before_publish=heartbeat,
            )
        except LockLostError:
            raise
        except Exception as error:
            logger.warning("Container %s failed: %s", name, error)
            records[name] = {
                "shot_key": plan.shot_key,
                "error": f"{type(error).__name__}: {error}",
                "recorded_at": datetime.now(UTC).isoformat(),
            }
            summary.failed.append(name)
            dirty = True
            continue
        record, result = written
        if record is not None:
            records[name] = record
            dirty = True
        if result is None:
            summary.skipped.append(name)
            continue
        summary.written.append(name)
        summary.problems += len(result.problems)
        if after_write is not None:
            after_write(name)
        if time.monotonic() - flushed >= flush_seconds:
            _checked(heartbeat)
            write_json_atomic(manifest_path, manifest)
            flushed, dirty = time.monotonic(), False
    if dirty or not manifest_path.exists():
        _checked(heartbeat)
        write_json_atomic(manifest_path, manifest)
    return summary


def _clear_earlier_temps(folder: Path, nonce: str) -> None:
    """Remove temp containers no writer holding the lock now will publish."""
    for stale in folder.glob(f"*.nxs*{_TMP_SUFFIX}"):
        if not (nonce and stale.name.endswith(f".{nonce}{_TMP_SUFFIX}")):
            try:
                stale.unlink(missing_ok=True)
            except OSError as error:  # Windows: still open by an overtaken writer
                logger.warning("Could not remove temp file %s: %s", stale, error)


def _checked(heartbeat: Callable[[], None] | None) -> None:
    if heartbeat is not None:
        heartbeat()


def _convert_one(
    plan: ShotPlan,
    target: Path,
    record: Mapping | None,
    read_path: ReadPath,
    *,
    nonce: str = "",
    before_publish: Callable[[], None] | None = None,
) -> tuple[dict | None, ShotResult | None]:
    """``(new manifest record or None, result or None when up to date)``."""
    stats = member_stats(plan, read_path)
    wanted = fingerprint(plan, read_path, stats)
    changed = _changed_files(target, record, wanted, stats)
    if changed == []:
        if record and record.get("fingerprint") == wanted:
            return None, None
        return _record(plan, target, wanted, None, stats), None
    notes = [
        f"{_recorded_name(recorded)} changed on disk since it was last converted "
        "(its size or mtime differ) while its event's sha256 did not; "
        "reconverted from the bytes on disk now"
        for recorded in changed or []
    ]
    result = write_container(
        target,
        plan,
        read_path,
        wanted,
        stats=stats,
        notes=notes,
        nonce=nonce,
        before_publish=before_publish,
    )
    return _record(plan, target, wanted, result, stats), result


def _record(
    plan: ShotPlan,
    target: Path,
    wanted: str,
    result: ShotResult | None,
    stats: Mapping[str, Stat],
) -> dict:
    record = {
        "shot_key": plan.shot_key,
        "fingerprint": wanted,
        "bytes": target.stat().st_size,
        "stats": dict(stats),
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    if result is not None:
        record["missing"] = result.missing
        record["problems"] = len(result.problems)
        record["detectors"] = result.detectors
        record["omitted"] = result.omitted
    return record


def _signature(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size, stat.st_ino


def run_conversion(
    master: Path,
    *,
    read_path: ReadPath,
    flush_seconds: float = 5.0,
    after_write: Callable[[str], None] | None = None,
    stale_after: float = LOCK_STALE_AFTER_S,
) -> list[ConversionSummary]:
    """Convert a campaign under its own lock; one pass per request.

    The lock is ``shots/.convert.lock``, never the master's. A worker that
    finds it taken leaves ``shots/.convert.pending`` and returns ``[]``; the
    holder makes another pass while that marker exists or the master changed
    during its pass, and looks for the marker once more after releasing the
    lock, so a request is never lost between the two. The lock is refreshed
    once per container; one not refreshed for ``stale_after`` seconds is
    reclaimed when it is another host's (on this host a live PID keeps it, so
    a stalled worker is never overtaken). A worker whose lock was taken over
    raises :class:`LockLostError` before it publishes anything more.
    """
    if not master.is_file():
        return []
    folder = shots_dir(master)
    folder.mkdir(parents=True, exist_ok=True)
    pending = folder / PENDING_NAME
    pending.touch()
    summaries: list[ConversionSummary] = []
    while pending.exists():
        try:
            with single_writer_lock(
                folder / _LOCK_STEM, stale_after=stale_after
            ) as lock:
                while pending.exists():
                    pending.unlink(missing_ok=True)
                    before = _signature(master)
                    if before is not None:
                        summaries.append(
                            convert_campaign(
                                master,
                                read_path=read_path,
                                flush_seconds=flush_seconds,
                                after_write=after_write,
                                heartbeat=lock.refresh,
                                nonce=lock.nonce,
                            )
                        )
                    if _signature(master) != before:
                        pending.touch()
        except BuilderAlreadyRunningError:
            break
    return summaries


UNASSIGNED_FOLDER = "_unassigned"


def campaign_masters(
    output_root: Path,
    *,
    include_unassigned: bool = False,
    folders: Iterable[str] | None = None,
) -> list[Path]:
    """Campaign masters under a multi-campaign output root.

    ``<root>/<folder>/<folder>.nxs`` (``campaign_builds`` layout). The
    ``_unassigned`` bucket (``unassigned.nxs``) only with
    ``include_unassigned`` or when named in ``folders``: its shots move to a
    campaign once resolved and would be converted twice. ``folders`` limits the
    run to those campaign folders.
    """
    wanted = set(folders) if folders is not None else None
    found = []
    for folder in sorted(p for p in output_root.iterdir() if p.is_dir()):
        if wanted is not None and folder.name not in wanted:
            continue
        is_bucket = folder.name == UNASSIGNED_FOLDER
        if is_bucket and not include_unassigned and wanted is None:
            continue
        stem = "unassigned" if is_bucket else folder.name
        master = folder / f"{stem}.nxs"
        if master.is_file():
            found.append(master)
    return found


__all__ = [
    "CONTAINER_PROFILE",
    "MANIFEST_NAME",
    "PENDING_NAME",
    "SHOTS_DIRNAME",
    "Acquisition",
    "ConversionSummary",
    "ShotPlan",
    "campaign_masters",
    "claim",
    "code_digest",
    "container_name",
    "convert_campaign",
    "fingerprint",
    "library_versions",
    "make_read_path",
    "member_stats",
    "plan_shots",
    "read_master",
    "run_conversion",
    "shots_dir",
    "write_container",
]
