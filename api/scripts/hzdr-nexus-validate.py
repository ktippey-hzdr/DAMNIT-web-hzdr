"""NeXus validation gate for a campaign's output (campaign output phase 5).

    <nds>/.venv/bin/python api/scripts/hzdr-nexus-validate.py \\
        --master <campaign>.nxs [--master ...] | --output-root <root> \\
        [--definitions hzdr/nxdl] [--strict-subentries]

Runs inside nexus-design-studio's environment (it has pynxtools; DAMNIT does
not depend on it) and imports nothing from ``damnit_api``. For each campaign:

* **master** -- NDS's structural check; pynxtools against the application
  definition its ``/entry`` declares (``NXhzdr_target``, overlaid from
  ``--definitions``); and every root link into ``shots/`` must resolve (the
  master never names a missing container).
* **containers** (``shots/<YYYYMMDD>_<number>.nxs`` naming this campaign) --
  NDS's structural check (NX classes, a top-level NXentry), and pynxtools on
  every ``NXsubentry`` that declares a ``definition`` (the mapping rows'
  claims, ``NXoptical_spectroscopy`` for the Irr8 spectrometers).

Exit 0: passed. Exit 1 (the gate): a structural error in the master or a
container, the master's entry not valid against its definition, a dangling
container link, or a subentry finding outside ``KNOWN_SUBENTRY_GAPS``.
Warnings are counted, never gating. A subentry whose only findings are the
known gaps is reported as *not certified* and gates only with
``--strict-subentries``: the concepts it lacks are mapping decisions
(shot-aligner ``config/mappings``), not something a converter may invent.
Exit 2: the gate could not run (no nexus-design-studio or pynxtools in this
Python, a missing ``--output-root`` or definitions directory); it writes a
report saying so, so an older passing one is not read as current.

Writes ``<campaign folder>/.validation.json`` (atomically) and prints one line
per campaign. ``--json`` prints the whole report as well.
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

try:
    import h5py
except ImportError:  # the wrong Python: say the gate could not run (exit 2)
    h5py = None

REPORT_NAME = ".validation.json"
CONTAINER_FILE = re.compile(r"^(?:\d{8}|unknown)_\d{6,}\.nxs$")
DEFAULT_DEFINITIONS = Path(__file__).resolve().parents[2] / "hzdr" / "nxdl"
CANNOT_RUN = 2

# What every Irr8 NXoptical_spectroscopy subentry lacks today, in shot-aligner's
# own build as in DAMNIT's (container-writer.md section 9): mapping decisions
# for a person. Any other subentry finding is new, and gates.
KNOWN_SUBENTRY_GAPS = tuple(
    re.compile(pattern)
    for pattern in (
        r"required group \S+/instrument/beam_TYPE hasn't been supplied",
        r"required group \S+/instrument/detector_TYPE hasn't been supplied",
        r"required attribute \S+/definition/@URL hasn't been supplied",
        r"required attribute \S+/definition/@version hasn't been supplied",
        r"required field \S+/experiment_type hasn't been supplied",
        r"/number_of_cycles should be one of the following Python types: .*NX_INT",
    )
)


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
    """pynxtools' messages, de-duplicated and grouped."""
    unique = list(dict.fromkeys(messages))
    required = [m for m in unique if "required" in m and "hasn't been supplied" in m]
    undocumented = [m for m in unique if "has no documentation" in m]
    other = [m for m in unique if m not in required and m not in undocumented]
    return {
        "required_missing": required,
        "undocumented": len(undocumented),
        "other": other,
    }


def _unexpected(findings: dict) -> list[str]:
    return [
        message
        for message in (*findings["required_missing"], *findings["other"])
        if not any(gap.search(message) for gap in KNOWN_SUBENTRY_GAPS)
    ]


class Validator:
    """NDS's checks and pynxtools, set up once for every file of a run.

    Uses two private NDS helpers (the definitions overlay and the pynxtools
    import); ``test_hzdr_nexus_validate`` breaks wherever NDS moves them.
    """

    def __init__(self, definitions: Path | None) -> None:
        from nexus_design_studio.core import validator as nds

        self._nds = nds
        self._tmp = None
        self.extra = frozenset()
        if definitions is not None:
            names = [p.name for p in definitions.glob("*.nxdl.xml")]
            if not names:
                msg = f"no *.nxdl.xml in {definitions}"
                raise FileNotFoundError(msg)
            if "pynxtools" in sys.modules:  # pragma: no cover - fresh process only
                msg = "pynxtools imported before its definitions path was set"
                raise RuntimeError(msg)
            self._tmp = tempfile.TemporaryDirectory(prefix="hzdr-nxdl-")
            nds._assemble_definitions_tree(definitions, Path(self._tmp.name))
            os.environ["NEXUS_DEF_PATH"] = self._tmp.name
            self.extra = frozenset(n.removesuffix(".nxdl.xml") for n in names)
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
        pynx = self._nds.run_pynxtools_validation(path)  # it captures its own findings
        result["entries"] = [e.model_dump() for e in pynx.entries]
        result["pynxtools_note"] = pynx.note
        result["findings"] = _sorted_findings(pynx.findings)
        if not pynx.entries:
            result["errors"].append(
                "no root entry declares a definition to validate against "
                f"({pynx.note or 'pynxtools checked nothing'})"
            )
        for entry in pynx.entries:
            if not entry.valid:
                result["errors"].append(
                    f"/{entry.entry} is not valid against {entry.definition}"
                )
        result["errors"] += _dangling_links(path)
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
                findings = _sorted_findings(messages)
                unexpected = _unexpected(findings)
                result["subentries"][name] = {
                    "definition": nxdl,
                    "valid": valid,
                    "findings": findings,
                    "unexpected": unexpected,
                }
                result["errors"] += [f"/entry/{name}: {m}" for m in unexpected]
        return result


def _dangling_links(master: Path) -> list[str]:
    """Root links into ``shots/`` whose container or ``/entry`` is not there."""
    problems = []
    with h5py.File(master, "r") as handle:
        for name in handle:
            link = handle.get(name, getlink=True)
            if not isinstance(link, h5py.ExternalLink):
                continue
            if not link.filename.startswith("shots/"):
                continue
            target = master.parent / link.filename
            try:
                with h5py.File(target, "r") as container:
                    if link.path not in container:
                        problems.append(f"/{name}: {link.filename} has no {link.path}")
            except OSError as error:
                problems.append(f"/{name}: {link.filename} cannot be opened ({error})")
    return problems


def _campaign_of(path: Path) -> str | None:
    with h5py.File(path, "r") as handle:
        key = handle.attrs.get("shot_key")
    key = _text(key) if key is not None else ""
    return key.rsplit(":", 2)[0] if key.count(":") >= 2 else None


def _write_json_atomic(path: Path, payload: dict) -> None:
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, indent=2, sort_keys=True))
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _experiment_id(master: Path) -> str:
    with h5py.File(master, "r") as handle:
        value = handle.attrs.get("experiment_id")
    return _text(value) if value is not None else master.stem


def validate_campaign(validator: Validator, master: Path, *, strict: bool) -> dict:
    master = master.resolve()
    report = {"master": master.name, "ran": True, "result": validator.master(master)}
    campaign = _experiment_id(master)
    containers = {}
    shots = master.parent / "shots"
    for path in sorted(shots.iterdir()) if shots.is_dir() else ():
        if not CONTAINER_FILE.match(path.name):
            continue
        try:
            if _campaign_of(path) not in {campaign, None}:
                continue  # another master's container in a shared folder
            containers[path.name] = validator.container(path)
        except Exception as error:
            containers[path.name] = {
                "ok": False,
                "errors": [f"could not be checked: {type(error).__name__}: {error}"],
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


def _print(report: dict) -> None:
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


def _masters(args) -> list[Path]:
    if args.master:
        return args.master
    root = args.output_root
    if not root.is_dir():
        msg = f"--output-root {root} is not a folder"
        raise FileNotFoundError(msg)
    return sorted(
        folder / f"{folder.name}.nxs"
        for folder in root.iterdir()
        if folder.is_dir() and (folder / f"{folder.name}.nxs").is_file()
    )


def _not_run(masters: list[Path], reason: str, *, write: bool) -> int:
    """Say the gate did not run, where a passing report could otherwise linger."""
    print(f"Validation could not run: {reason}", file=sys.stderr)
    for master in masters:
        if write and master.is_file():
            _write_json_atomic(
                master.resolve().parent / REPORT_NAME,
                {
                    "master": master.name,
                    "ran": False,
                    "reason": reason,
                    "summary": {"passed": None},
                    "validated_at": datetime.now(UTC).isoformat(),
                },
            )
    return CANNOT_RUN


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

    if h5py is None:
        return _not_run([], "h5py is not installed in this Python", write=False)
    try:
        masters = _masters(args)
    except FileNotFoundError as error:
        return _not_run([], str(error), write=False)
    try:
        validator = Validator(args.definitions)
    except (ImportError, AttributeError, FileNotFoundError, RuntimeError) as error:
        reason = (
            f"{type(error).__name__}: {error} -- run it with a Python that has "
            "nexus-design-studio and pynxtools"
        )
        return _not_run(masters, reason, write=not args.no_write)

    failed = False
    reports = []
    for master in masters:
        if not master.is_file():
            print(f"Validation ({master.name}): no published master")
            continue
        try:
            report = validate_campaign(validator, master, strict=args.strict_subentries)
        except Exception as error:
            print(f"Validation ({master.name}): FAILED; could not be checked: {error}")
            failed = True
            continue
        reports.append(report)
        if not args.no_write:
            _write_json_atomic(master.resolve().parent / REPORT_NAME, report)
        _print(report)
        failed = failed or not report["summary"]["passed"]
    if args.json:
        print(json.dumps(reports, indent=2, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
