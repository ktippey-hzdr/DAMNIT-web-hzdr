"""shot-aligner's per-instrument mapping rows, applied to a shot container in h5py.

Campaign output phase 4b. A port of shot-aligner's ``mappings.apply_to`` and
the helpers it needs (``shot_aligner/scripts/shotalign/mappings.py``), from
nexusformat to h5py, with the same rules:

* **Additive.** A row links a dataset the pack already wrote to the path a
  person agreed, as a hard link (an ``NXlink`` in shot-aligner's terms), so
  both names stay valid and a wrong row is undone by deleting it. The one
  exception is a row with ``value_transform`` or ``convert_to_unit``: the
  number is different, so it writes a new dataset stamped ``derived_from``.
* **Reported, never forced.** A row whose source this acquisition did not
  write, whose target another instrument already claimed, or whose target is a
  group the build wrote, is reported as a problem and skipped; one bad row
  costs that row, not the container.
* **Decisions are stamped** on what they touch (``mapped_from``,
  ``mapping_status``, ``nds_local_name``, ...), so a reader can tell a reviewed
  mapping from the writer's own default without the mapping files.

The mapping files themselves are vendored byte for byte from shot-aligner's
``config/mappings/`` into ``vendor/mappings/`` (``sync-hzdr-packs``), and found
here by the catalogue's ``instrument.id``. They are data, not code: a change
to one rebuilds only the containers holding that instrument (the container
fingerprint carries each mapping's sha256).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from functools import cache
from typing import TYPE_CHECKING, Any

import h5py

from .vendor import HERE as VENDOR

if TYPE_CHECKING:
    from pathlib import Path

MAPPING_DIR = VENDOR / "mappings"
REVIEWED = "reviewed"

# mappings.TRANSFORM_FACTORS and UNIT_SCALE, which NDS's numbers hold (a test in
# shot-aligner ties the two). A row naming anything else is reported.
TRANSFORM_FACTORS = {
    "gaussian_sigma_to_fwhm": 2.354_820_045_030_949_3,
    "gaussian_one_over_e2_radius_to_fwhm": 1.177_410_022_515_474_7,
    "one_over_e2_width_to_radius": 0.5,
}
UNIT_SCALE = {
    ("mm", "um"): 1000.0,
    ("um", "mm"): 0.001,
    ("m", "mm"): 1000.0,
    ("mm", "m"): 0.001,
    ("m", "um"): 1_000_000.0,
    ("um", "m"): 0.000_001,
    ("s", "ms"): 1000.0,
    ("ms", "s"): 0.001,
    ("ms", "us"): 1000.0,
    ("us", "ms"): 0.001,
    ("s", "us"): 1_000_000.0,
    ("us", "s"): 0.000_001,
}
# Micro sign and Greek mu, spelled out: both mean micro.
UNIT_SPELLINGS = {
    chr(0xB5) + "s": "us",
    chr(0x3BC) + "s": "us",
    chr(0xB5) + "m": "um",
    chr(0x3BC) + "m": "um",
}

# The groups an application definition puts directly under its entry.
_GROUP_CLASSES = {
    "entry": "NXentry",
    "entry/instrument": "NXinstrument",
    "entry/sample": "NXsample",
    "entry/process": "NXprocess",
    "entry/data": "NXdata",
    "entry/collection": "NXcollection",
}


@dataclass
class InstrumentMapping:
    """One instrument's rows, as shot-aligner's ``mappings.InstrumentMapping``."""

    instrument: str
    file_name: str
    rows: list[dict] = field(default_factory=list)
    definition: str | None = None
    status: str = REVIEWED
    instrument_id: str = ""
    sha256: str = ""

    @property
    def is_reviewed(self) -> bool:
        return self.status == REVIEWED

    @property
    def written_rows(self) -> list[dict]:
        """Rows that put something in a container: a source, and not an attribute."""
        return [
            row
            for row in self.rows
            if (row.get("source") or "").strip()
            and not (row.get("nexus_attribute") or "").strip()
        ]


def _load(path: Path) -> InstrumentMapping:
    data = path.read_bytes()
    raw = json.loads(data)
    rows = raw.get("mappings") or []
    if not isinstance(rows, list):
        msg = f"{path.name}: 'mappings' is not a list"
        raise ValueError(msg)
    return InstrumentMapping(
        instrument=raw.get("instrument") or path.stem,
        file_name=path.name,
        rows=rows,
        definition=raw.get("definition") or None,
        status=raw.get("status") or REVIEWED,
        instrument_id=raw.get("instrumentId") or "",
        sha256=hashlib.sha256(data).hexdigest(),
    )


@cache
def by_id() -> dict[str, InstrumentMapping]:
    """Every vendored mapping that records an ``instrument.id``, by that id."""
    if not MAPPING_DIR.is_dir():
        return {}
    found = {}
    for path in sorted(MAPPING_DIR.glob("*.json")):
        if path.name.startswith("_"):
            continue
        mapping = _load(path)
        if mapping.instrument_id:
            found[mapping.instrument_id] = mapping
    return found


def for_instrument(instrument_id: str) -> InstrumentMapping | None:
    return by_id().get(instrument_id)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def is_shot_level(source: str) -> bool:
    """Whether a source names the shot's entry (``/entry/...``), not the detector."""
    return source.strip().startswith("/entry/")


def source_path(detector_path: str, source: str) -> str:
    """Where a row's ``source`` is in the container, without a leading slash."""
    source = source.strip()
    if is_shot_level(source):
        return source.strip("/")
    return f"{detector_path.strip('/')}/{source.strip('/')}"


def subentry_name(detector_path: str) -> str:
    """The NXsubentry a diagnostic's application definition is claimed in."""
    return detector_path.strip("/").rsplit("/", 1)[-1]


def _declared_class(walked: str) -> str | None:
    """The class for a group on a mapped path, instance names included."""
    if walked in _GROUP_CLASSES:
        return _GROUP_CLASSES[walked]
    head, _, last = walked.rpartition("/")
    stem = last.split("_")[0]
    return _GROUP_CLASSES.get(f"{head}/{stem}" if head else stem)


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


def _unit(text: str) -> str:
    text = (text or "").strip()
    return UNIT_SPELLINGS.get(text, text)


def is_derived(row: dict) -> bool:
    """Whether this row changes the value rather than linking to it."""
    return bool(
        (row.get("value_transform") or "").strip()
        or (row.get("convert_to_unit") or "").strip()
    )


def derive(value: Any, units: str, row: dict) -> tuple[float, str, dict]:
    """Apply this row's declared transform and conversion. Raises on anything else."""
    note: dict = {}
    result = float(value)
    name = (row.get("value_transform") or "").strip()
    if name:
        factor = TRANSFORM_FACTORS[name]
        result *= factor
        note["value_transform"] = name
        note["value_transform_factor"] = factor
    target = _unit(row.get("convert_to_unit") or "")
    source_unit = _unit(units)
    if target and target != source_unit:
        result *= UNIT_SCALE[source_unit, target]
        note["units_converted_from"] = units
        units = target
    elif target:
        units = target
    return result, units, note


# ---------------------------------------------------------------------------
# h5py helpers standing in for nexusformat's
# ---------------------------------------------------------------------------


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if hasattr(value, "item"):
        return _text(value.item())
    return "" if value is None else str(value)


def _nx_class(node: Any) -> str:
    return _text(node.attrs.get("NX_class", "")) if node is not None else ""


def _group(handle: h5py.File, path: str, nx_class: str) -> h5py.Group:
    group = handle.create_group(path)
    group.attrs["NX_class"] = nx_class
    return group


def _signal(handle: h5py.File, nxdata_path: str) -> h5py.Dataset | None:
    group = handle[nxdata_path]
    name = _text(group.attrs.get("signal", ""))
    if not name or name not in group:
        return None
    node = group[name]
    return node if isinstance(node, h5py.Dataset) else None


def _signal_is(handle: h5py.File, target: str, source: str) -> bool:
    """Whether ``target`` is an NXdata whose signal is ``source`` itself."""
    if target not in handle or not isinstance(handle[target], h5py.Group):
        return False
    if _nx_class(handle[target]) != "NXdata" or source not in handle:
        return False
    signal = _signal(handle, target)
    return signal is not None and signal.id == handle[source].id


def _ensure_groups(handle: h5py.File, path: str, leaf_class: str | None) -> None:
    """Create every group on the way to a mapped path, with a sensible class."""
    parts = [p for p in path.split("/") if p]
    sub = (
        len(parts) >= 2
        and f"{parts[0]}/{parts[1]}" in handle
        and _nx_class(handle[f"{parts[0]}/{parts[1]}"]) == "NXsubentry"
    )
    walked = ""
    for n, part in enumerate(parts):
        walked = f"{walked}/{part}" if walked else part
        if walked in handle:
            continue
        if n == len(parts) - 1 and leaf_class:
            name = leaf_class
        elif sub and n == 3 and parts[2] == "instrument":
            name = "NXdetector"
        elif sub:
            name = _declared_class("/".join(["entry", *parts[2 : n + 1]]))
        else:
            name = _declared_class(walked)
        _group(
            handle, walked, name if (name or "").startswith("NX") else "NXcollection"
        )


def _ensure_subentry(
    handle: h5py.File,
    subentry: str,
    mapping: InstrumentMapping,
    problems: list[str],
) -> bool:
    """Create the NXsubentry carrying this instrument's definition claim."""
    if subentry in handle:
        if _nx_class(handle[subentry]) == "NXsubentry":
            return True
        problems.append(
            f"{mapping.instrument}: its subentry /{subentry} is already a "
            f"{_nx_class(handle[subentry])}, so its {mapping.definition} rows were "
            "not written. Rename one of them in the campaign config."
        )
        return False
    group = _group(handle, subentry, "NXsubentry")
    definition = group.create_dataset("definition", data=mapping.definition)
    definition.attrs["mapping_status"] = mapping.status
    definition.attrs["description"] = (
        f"claimed by the {mapping.status} mapping for {mapping.instrument}; "
        "everything in this subentry is a link to where the container already "
        f"holds it. See config/mappings/{mapping.file_name}"
    )
    return True


def _mirror_plot(handle: h5py.File, subentry: str, detector_path: str) -> None:
    """Make the subentry's NXdata plot what the diagnostic's own data plots."""
    plotted = f"{detector_path.strip('/')}/data"
    if plotted not in handle or _nx_class(handle[plotted]) != "NXdata":
        return
    signal = _signal(handle, plotted)
    if signal is None:
        return
    declared = handle[plotted].attrs.get("axes")
    if declared is None:
        names: list[str] = []
    elif isinstance(declared, (str, bytes)):
        names = [_text(declared)]
    else:
        names = [_text(a) for a in declared]
    axes = [
        handle[f"{plotted}/{name}"].id
        for name in names
        if name and f"{plotted}/{name}" in handle
    ]
    groups: list[h5py.Group] = []
    handle[subentry].visititems(
        lambda _name, node: (
            groups.append(node) if isinstance(node, h5py.Group) else None
        )
    )
    for group in groups:
        if _nx_class(group) != "NXdata" or "signal" in group.attrs:
            continue
        by_origin = {
            child.id: name
            for name, child in group.items()
            if isinstance(child, h5py.Dataset) and "source_path" in child.attrs
        }
        chosen = by_origin.get(signal.id)
        if not chosen:
            continue
        group.attrs["signal"] = chosen
        named = [by_origin.get(axis) for axis in axes]
        if named and all(named):
            group.attrs["axes"] = named if len(named) > 1 else named[0]


# ---------------------------------------------------------------------------
# apply_to
# ---------------------------------------------------------------------------


def _stale_placement(
    row: dict,
    detector_path: str,
    detector: str,
    inside: bool,
    mapping: InstrumentMapping,
) -> str | None:
    """Why a row placed for another detector or machine is not written, if it is."""
    target = row["nexus_path"].strip("/")
    instance = (row.get("group_instance") or "").strip()
    if (
        mapping.definition
        and instance
        and instance != detector
        and target.startswith(f"entry/{instance}/")
    ):
        return (
            f"{mapping.instrument}: mapping row {row['local_name']!r} was placed in "
            f"the subentry /entry/{instance}, but this diagnostic is now written as "
            f"{detector}. Run `align.py map <date> --place` to move the rows with it."
        )
    if (
        instance == detector
        and not inside
        and not target.startswith(detector_path.strip("/") + "/")
        and target != detector_path.strip("/")
    ):
        return (
            f"{mapping.instrument}: mapping row {row['local_name']!r} was placed at "
            f"{row['nexus_path']}, but this diagnostic is written under "
            f"/{detector_path.strip('/')} -- its family changed since. Run "
            "`align.py map <date> --place` to move the rows with it."
        )
    return None


def _occupied(handle: h5py.File, target: str, row: dict, instrument: str) -> str:
    node = handle[target]
    taken = _text(node.attrs.get("mapped_from", ""))
    name = row["local_name"]
    if isinstance(node, h5py.Group):
        return (
            f"{instrument}: mapping row {name!r} targets /{target}, which this "
            f"container holds as an {_nx_class(node) or 'NXgroup'} group, not a "
            "dataset. Nothing was linked; the row's target needs a ruling."
        )
    if taken:
        return (
            f"{instrument}: mapping row {name!r} would write /{target}, which "
            f"{taken} already claimed. Both readings under entry/<instrument>/ are "
            "unaffected; to give this one the shared path too, map it to a "
            "distinct group."
        )
    return (
        f"{instrument}: mapping row {name!r} would write /{target}, which already "
        "holds a dataset no mapping wrote (the pack's or the build's own). It was "
        "left as it is; the row's target needs a ruling."
    )


def _stamp(
    node: h5py.Dataset | h5py.Group,
    row: dict,
    mapping: InstrumentMapping,
    source: str,
    derived: dict,
    decision: str,
) -> None:
    stamp = node.attrs
    for key, value in derived.items():
        stamp[key] = value
    if derived:
        stamp["derived_from"] = f"/{source}"
        stamp["NOTE"] = (
            f"DERIVED, not a link: this value was computed from /{source} by the "
            f"{decision} for {mapping.instrument}. The number there is the one the "
            "instrument reported."
        )
    stamp["mapped_from"] = mapping.instrument
    stamp["mapping_status"] = mapping.status
    stamp["nds_local_name"] = row["local_name"]
    stamp["nds_status"] = row.get("status") or "reviewed"
    if row.get("confidence") is not None:
        stamp["nds_confidence"] = row["confidence"]
    if row.get("registry_key"):
        stamp["registry_key"] = row["registry_key"]
    if row.get("note"):
        stamp["description"] = row["note"]
    stamp["source_path"] = f"/{source}"


def _write_value(
    handle: h5py.File,
    row: dict,
    source: str,
    target: str,
    mapping: InstrumentMapping,
    problems: list[str],
) -> dict | None:
    """Write a linked or derived row at ``target``; its derivation, or None."""
    parent, _, leaf = target.rpartition("/")
    if not is_derived(row):
        try:
            handle[parent][leaf] = handle[source]  # a hard link: one object, two names
        except (KeyError, OSError, ValueError, TypeError) as error:
            problems.append(f"{mapping.instrument}: could not link /{target}: {error}")
            return None
        return {}
    node = handle[source]
    try:
        value, units, derived = derive(
            node[()], _text(node.attrs.get("units", "")), row
        )
    except (KeyError, TypeError, ValueError) as error:
        problems.append(
            f"{mapping.instrument}: {row['local_name']!r} could not be derived from "
            f"{row['source']!r}: {error}. Nothing was written for it; the row is a "
            "decision this data cannot carry out."
        )
        return None
    created = handle[parent].create_dataset(leaf, data=value)
    if units:
        created.attrs["units"] = units
    return derived


def _refusal(
    handle: h5py.File, row: dict, detector_path: str, mapping: InstrumentMapping
) -> str | None:
    """Why a row cannot be written here at all, or None."""
    detector = subentry_name(detector_path)
    target = row["nexus_path"].strip("/")
    inside = bool(mapping.definition) and target.startswith(f"entry/{detector}/")
    stale = _stale_placement(row, detector_path, detector, inside, mapping)
    if stale:
        return stale
    if source_path(detector_path, row["source"]) not in handle:
        return (
            f"{mapping.instrument}: mapping row {row['local_name']!r} points at "
            f"{row['source']!r}, which this acquisition did not write"
        )
    return None


def _write_row(
    handle: h5py.File,
    row: dict,
    detector_path: str,
    mapping: InstrumentMapping,
    problems: list[str],
) -> bool:
    """Write one row; True when it landed inside the instrument's subentry."""
    decision = (
        "reviewed mapping" if mapping.is_reviewed else "unreviewed proposed mapping"
    )
    refusal = _refusal(handle, row, detector_path, mapping)
    if refusal:
        problems.append(refusal)
        return False
    subentry = f"entry/{subentry_name(detector_path)}"
    source = source_path(detector_path, row["source"])
    target = row["nexus_path"].strip("/")
    inside = bool(mapping.definition) and target.startswith(subentry + "/")
    in_place = not is_derived(row) and (
        target == source or _signal_is(handle, target, source)
    )
    if in_place and is_shot_level(row["source"]):
        return False  # the shot's own field, shared by every instrument
    if not in_place and target in handle:
        problems.append(_occupied(handle, target, row, mapping.instrument))
        return False
    if inside and not _ensure_subentry(handle, subentry, mapping, problems):
        return False
    if in_place:
        _stamp(handle[source], row, mapping, source, {}, decision)
        return inside
    _ensure_groups(handle, target.rpartition("/")[0], row.get("nexus_class"))
    derived = _write_value(handle, row, source, target, mapping, problems)
    if derived is None:
        return False
    _stamp(handle[target], row, mapping, source, derived, decision)
    return inside


def apply_to(
    handle: h5py.File,
    detector_path: str,
    mapping: InstrumentMapping,
    *,
    sole_instrument: bool = False,
) -> list[str]:
    """Link this instrument's mapped fields to their NeXus paths; return problems.

    shot-aligner's ``mappings.apply_to``: see the module docstring for the
    rules. ``sole_instrument`` says whether this entry holds only this
    instrument, the one case where ``entry/definition`` can be claimed.
    """
    problems: list[str] = []
    detector_path = detector_path.strip("/")
    decision = (
        "reviewed mapping" if mapping.is_reviewed else "unreviewed proposed mapping"
    )
    in_subentry = sum(
        _write_row(handle, row, detector_path, mapping, problems)
        for row in mapping.written_rows
    )
    if not mapping.definition:
        return problems
    subentry = f"entry/{subentry_name(detector_path)}"
    if detector_path in handle:
        group = handle[detector_path]
        group.attrs["nds_definition"] = mapping.definition
        group.attrs["mapping_status"] = mapping.status
        group.attrs["nds_definition_note"] = (
            f"the {decision} for {mapping.instrument} says its data satisfies "
            f"{mapping.definition}; see config/mappings/{mapping.file_name}"
        )
        if in_subentry:
            group.attrs["nds_subentry"] = f"/{subentry}"
    if in_subentry:
        _mirror_plot(handle, subentry, detector_path)
    elif not sole_instrument:
        problems.append(
            f"{mapping.instrument}: claims {mapping.definition}, which is recorded "
            "on its detector group but not on the entry -- an application "
            "definition describes one instrument's entry, and this container holds "
            "several. Build a single-instrument file to claim it."
        )
    elif "entry/definition" in handle:
        existing = _text(handle["entry/definition"][()])
        if existing != mapping.definition:
            problems.append(
                f"{mapping.instrument}: claims definition {mapping.definition!r} but "
                f"the entry already declares {existing!r}; the first one written "
                "stands and this is left off"
            )
    else:
        definition = handle["entry"].create_dataset(
            "definition", data=mapping.definition
        )
        definition.attrs["mapping_status"] = mapping.status
        definition.attrs["description"] = (
            f"claimed by the {decision} for {mapping.instrument}; see "
            f"config/mappings/{mapping.file_name}"
        )
    return problems
