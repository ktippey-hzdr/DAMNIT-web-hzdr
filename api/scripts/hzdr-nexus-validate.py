"""NeXus validation gate for a campaign's output (campaign output phase 5).

    uv run --project <nexus-design-studio> python api/scripts/hzdr-nexus-validate.py \\
        --master <campaign>.nxs [--master ...] | --output-root <root> \\
        [--definitions hzdr/nxdl] [--strict-subentries]

Runs inside nexus-design-studio's environment (it has pynxtools; DAMNIT does
not depend on it) and imports nothing from ``damnit_api``. For each campaign:

* **master** -- NDS's structural check, and pynxtools against the application
  definition its ``/entry`` declares (``NXhzdr_target``, overlaid from
  ``--definitions``). Run from the master's folder: pynxtools resolves the
  relative links to the containers against its working directory.
* **containers** (``shots/<YYYYMMDD>_<number>.nxs``) -- NDS's structural
  check (NX classes, a top-level NXentry), and pynxtools on every
  ``NXsubentry`` that declares a ``definition`` (the mapping rows' claims,
  ``NXoptical_spectroscopy`` for the Irr8 spectrometers).

Errors gate (exit 1): a structural error in the master or a container, or the
master's entry not valid against its definition. Warnings are counted and
reported, never gating. A subentry not valid against its definition is
reported as *not certified* with what it lacks, and gates only with
``--strict-subentries``: the concepts it lacks are mapping decisions
(shot-aligner ``config/mappings``), not something a converter may invent.

Writes ``<campaign folder>/.validation.json`` (atomically) and prints one line
per campaign. ``--json`` prints the whole report instead.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import h5py

REPORT_NAME = ".validation.json"
CONTAINER_FILE = re.compile(r"^(?:\d{8}|unknown)_\d{6,}\.nxs$")
DEFAULT_DEFINITIONS = Path(__file__).resolve().parents[2] / "hzdr" / "nxdl"


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@contextlib.contextmanager
def _captured():
    capture = _Capture()
    logger = logging.getLogger("pynxtools")
    # pynxtools prints every finding to stderr itself; the report has them all.
    saved = (logger.handlers[:], logger.propagate)
    logger.handlers[:] = [capture]
    logger.propagate = False
    try:
        yield capture.messages
    finally:
        logger.handlers[:], logger.propagate = saved


def _text(value) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def _sorted_findings(messages: list[str]) -> dict:
    """pynxtools' messages, grouped: what is missing, what is undocumented, the rest."""
    required = [m for m in messages if "required" in m and "hasn't been supplied" in m]
    undocumented = [m for m in messages if "has no documentation" in m]
    other = [m for m in messages if m not in required and m not in undocumented]
    return {
        "required_missing": required,
        "undocumented": len(undocumented),
        "other": other,
    }


class Validator:
    """NDS's checks and pynxtools, set up once for every file of a run."""

    def __init__(self, definitions: Path | None) -> None:
        from nexus_design_studio.core import validator as nds

        self._nds = nds
        self._tmp = None
        self.extra = frozenset()
        if definitions is not None:
            if "pynxtools" in sys.modules:  # pragma: no cover - fresh process only
                msg = "pynxtools imported before its definitions path was set"
                raise RuntimeError(msg)
            self._tmp = tempfile.TemporaryDirectory(prefix="hzdr-nxdl-")
            nds._assemble_definitions_tree(definitions, Path(self._tmp.name))
            os.environ["NEXUS_DEF_PATH"] = self._tmp.name
            self.extra = frozenset(
                p.name.removesuffix(".nxdl.xml") for p in definitions.glob("*.nxdl.xml")
            )
        self._against = nds._load_pynxtools_validator()

    def structural(self, path: Path) -> dict:
        report = self._nds.validate_nexus_file(path, extra_definitions=self.extra)
        return {
            "ok": report.ok,
            "errors": [f"{i.path}: {i.message}" for i in report.errors],
            "warnings": [f"{i.path}: {i.message}" for i in report.warnings],
        }

    def master(self, path: Path) -> dict:
        result = self.structural(path)
        with _captured() as messages:
            pynx = self._nds.run_pynxtools_validation(path)
        result["entries"] = [e.model_dump() for e in pynx.entries]
        result["pynxtools_ok"] = pynx.ok and pynx.available
        result["pynxtools_note"] = pynx.note
        result["findings"] = _sorted_findings([*pynx.findings, *messages])
        if not pynx.available:
            result["errors"].append(f"pynxtools is not available: {pynx.note}")
        for entry in pynx.entries:
            if not entry.valid:
                result["errors"].append(
                    f"/{entry.entry} is not valid against {entry.definition}"
                )
        return result

    def container(self, path: Path) -> dict:
        result = self.structural(path)
        result["subentries"] = {}
        with h5py.File(path, "r") as handle:
            entry = handle.get("entry")
            for name, group in entry.items() if isinstance(entry, h5py.Group) else ():
                if not isinstance(group, h5py.Group):
                    continue
                if _text(group.attrs.get("NX_class", "")) != "NXsubentry":
                    continue
                definition = group.get("definition")
                if not isinstance(definition, h5py.Dataset):
                    continue
                nxdl = _text(definition[()])
                with _captured() as messages:
                    try:
                        valid = bool(self._against(nxdl, group, str(path), False))
                    except Exception as error:
                        messages.append(f"pynxtools raised: {error}")
                        valid = False
                result["subentries"][name] = {
                    "definition": nxdl,
                    "valid": valid,
                    "findings": _sorted_findings(messages),
                }
        return result


def _write_json_atomic(path: Path, payload: dict) -> None:
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(path)


def validate_campaign(validator: Validator, master: Path, *, strict: bool) -> dict:
    master = master.resolve()
    here = Path.cwd()
    os.chdir(master.parent)  # pynxtools resolves relative links from here
    try:
        report = {"master": master.name, "result": validator.master(master)}
    finally:
        os.chdir(here)
    containers = {}
    shots = master.parent / "shots"
    if shots.is_dir():
        for path in sorted(shots.iterdir()):
            if CONTAINER_FILE.match(path.name):
                try:
                    containers[path.name] = validator.container(path)
                except OSError as error:
                    containers[path.name] = {
                        "ok": False,
                        "errors": [f"unreadable: {error}"],
                        "warnings": [],
                        "subentries": {},
                    }
    report["containers"] = containers
    report["summary"] = _summary(report, strict=strict)
    report["validated_at"] = datetime.now(UTC).isoformat()
    return report


def _summary(report: dict, *, strict: bool) -> dict:
    master = report["result"]
    containers = report["containers"].values()
    subentries = [s for c in containers for s in c["subentries"].values()]
    container_errors = sum(len(c["errors"]) for c in containers)
    uncertified = sum(not s["valid"] for s in subentries)
    passed = not master["errors"] and not container_errors
    if strict:
        passed = passed and not uncertified
    return {
        "passed": passed,
        "master_errors": len(master["errors"]),
        "master_warnings": len(master["warnings"])
        + len(master["findings"]["required_missing"])
        + len(master["findings"]["other"]),
        "containers": len(report["containers"]),
        "container_errors": container_errors,
        "container_warnings": sum(len(c["warnings"]) for c in containers),
        "subentries": len(subentries),
        "subentries_certified": len(subentries) - uncertified,
        "strict_subentries": strict,
    }


def _line(report: dict) -> str:
    s = report["summary"]
    return (
        f"Validation ({report['master']}): {'passed' if s['passed'] else 'FAILED'}; "
        f"master {s['master_errors']} error(s), {s['master_warnings']} warning(s); "
        f"{s['containers']} container(s), {s['container_errors']} error(s), "
        f"{s['container_warnings']} warning(s); subentries "
        f"{s['subentries_certified']}/{s['subentries']} certified"
    )


def _masters(args) -> list[Path]:
    if args.master:
        return args.master
    root = args.output_root
    return sorted(
        folder / f"{folder.name}.nxs"
        for folder in root.iterdir()
        if folder.is_dir() and (folder / f"{folder.name}.nxs").is_file()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--master", action="append", type=Path)
    where.add_argument("--output-root", type=Path)
    parser.add_argument(
        "--definitions",
        type=Path,
        default=DEFAULT_DEFINITIONS,
        help="NXDL overlay (default: this repository's hzdr/nxdl)",
    )
    parser.add_argument(
        "--strict-subentries",
        action="store_true",
        help="also fail when a definition subentry is not certified",
    )
    parser.add_argument("--json", action="store_true", help="print the full report")
    parser.add_argument(
        "--no-write", action="store_true", help=f"do not write {REPORT_NAME}"
    )
    args = parser.parse_args(argv)

    validator = Validator(args.definitions)
    failed = False
    reports = []
    for master in _masters(args):
        if not master.is_file():
            print(f"Validation ({master.name}): no published master")
            continue
        report = validate_campaign(validator, master, strict=args.strict_subentries)
        reports.append(report)
        if not args.no_write:
            _write_json_atomic(master.resolve().parent / REPORT_NAME, report)
        print(_line(report))
        result = report["result"]
        for error in result["errors"][:20]:
            print(f"  master: {error}")
        if result["errors"]:  # what pynxtools said, so the log explains it
            findings = result["findings"]
            for finding in (*findings["required_missing"], *findings["other"])[:20]:
                print(f"    {finding}")
        for name, container in report["containers"].items():
            for error in container["errors"][:5]:
                print(f"  {name}: {error}")
        failed = failed or not report["summary"]["passed"]
    if args.json:
        print(json.dumps(reports, indent=2, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
