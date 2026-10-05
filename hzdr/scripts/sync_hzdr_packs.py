"""Check (or apply) the vendored copy of shot-aligner's readers and pack helpers.

The one implementation behind ``sync-hzdr-packs.ps1`` and
``sync-hzdr-packs.sh``; stdlib only. Sibling of ``sync_hzdr_reference.py``,
whose helpers it reuses.

shot-aligner owns the format readers (``polina/img_csv.py``,
``polina/irr8.py``), the helpers its packs write through
(``camera_metadata.py``, ``nxwrite.py``) and the packs' declarative manifests
(``diagnostics/<pack>.json``). DAMNIT-web-hzdr vendors exactly those files,
byte for byte, into ``api/src/damnit_api/metadata/hzdr_packs/vendor/`` (plan
decision 1) and rewrites only the packs themselves, to h5py (decision 2).
``SOURCE.json`` there records the shot-aligner commit and every file's sha256.

Check (default): fails when a vendored file differs from its hash in
SOURCE.json or from shot-aligner's. Without the sibling checkout only the
hashes are checked.

Apply (--apply): copies shot-aligner's files over and rewrites SOURCE.json.
Refuses when a source has uncommitted changes in shot-aligner unless --force.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sync_hzdr_reference import (  # noqa: E402
    DAMNIT_ROOT,
    REPOSITORY,
    _default_repo,
    _git,
    _sha256,
)

DEST = DAMNIT_ROOT / "api" / "src" / "damnit_api" / "metadata" / "hzdr_packs" / "vendor"
MANIFEST = "SOURCE.json"
_PACKS = "shot_aligner/scripts/shotalign/diagnostics"

# Vendored name -> path in shot-aligner. Only what the rewritten packs and the
# container writer use: the two readers, the camera sidecar helpers, nxwrite's
# naming and scale helpers, each pack's manifest (suffixes, name patterns,
# scale type) and the instrument catalogue.
FILES = {
    "img_csv.py": "polina/img_csv.py",
    "irr8.py": "polina/irr8.py",
    "camera_metadata.py": "shot_aligner/scripts/shotalign/camera_metadata.py",
    "nxwrite.py": "shot_aligner/scripts/shotalign/nxwrite.py",
    "camera_png_csv.json": f"{_PACKS}/camera_png_csv.json",
    "spectrometer_irr8.json": f"{_PACKS}/spectrometer_irr8.json",
    "sequence_frames.json": f"{_PACKS}/sequence_frames.json",
    # NDS's DRACO instrument catalogue as shot-aligner pins it: the container
    # writer names NXinstrument/NXdetector groups from its family,
    # instrument_name and detector_name (campaign output phase 3).
    "hzdr-draco-0.2.0.json": (
        "shot_aligner/config/instrument-catalogue/hzdr-draco-0.2.0.json"
    ),
}


# shot-aligner's per-instrument mapping rows (campaign output phase 4b), every
# file in its config/mappings/ but the README, vendored as mappings/<name>.
_MAPPINGS = "shot_aligner/config/mappings"


def _files(repo: Path | None, recorded: dict | None = None) -> dict[str, str]:
    """``FILES`` plus the mapping files: shot-aligner's when it is there, else
    the ones ``SOURCE.json`` records (so a check without the sibling still
    finds a missing or changed copy)."""
    files = dict(FILES)
    if repo is not None and (repo / _MAPPINGS).is_dir():
        for path in sorted((repo / _MAPPINGS).glob("*.json")):
            files[f"mappings/{path.name}"] = f"{_MAPPINGS}/{path.name}"
    elif recorded is not None:
        for name, entry in recorded["files"].items():
            if name.startswith("mappings/"):
                files[name] = entry["from"]
    return files


def check(repo: Path, dest: Path = DEST) -> list[str]:
    problems = []
    recorded = json.loads((dest / MANIFEST).read_text(encoding="utf-8"))
    present = (repo / FILES["img_csv.py"]).is_file()
    files_now = _files(repo if present else None, recorded)
    if set(recorded["files"]) != set(files_now):
        problems.append(
            f"{MANIFEST} lists {sorted(set(recorded['files']) ^ set(files_now))} "
            "differently from the files this script vendors"
        )
    vendored = {f"mappings/{p.name}" for p in (dest / "mappings").glob("*.json")}
    for stray in sorted(vendored - set(files_now)):
        problems.append(f"{stray}: vendored but not recorded (it would be loaded)")
    for name in files_now:
        path = dest / name
        if not path.is_file():
            problems.append(f"{name}: not vendored")
        elif (
            name in recorded["files"]
            and _sha256(path) != recorded["files"][name]["sha256"]
        ):
            problems.append(f"{name}: does not match its hash in {MANIFEST}")

    if not present:
        print(f"  Skipped the shot-aligner comparison (not found at {repo})")
        return problems
    for name, source in files_now.items():
        upstream, vendored = repo / source, dest / name
        if not upstream.is_file():
            problems.append(f"{name}: {source} no longer in shot-aligner")
        elif vendored.is_file() and upstream.read_bytes() != vendored.read_bytes():
            problems.append(f"{name}: differs from shot-aligner's {source}")
    head = _git(repo, "log", "-1", "--format=%h", "--", *files_now.values())
    if not problems and head and head != recorded.get("commit"):
        print(
            f"  Note: files agree, but {MANIFEST} names commit "
            f"{recorded.get('commit')} and shot-aligner's sources are at {head}; "
            "--apply re-pins it."
        )
    return problems


def apply(repo: Path, force: bool, dest: Path = DEST) -> None:
    if not (repo / _MAPPINGS).is_dir():
        sys.exit(f"not found in {repo}: {_MAPPINGS} (refusing to drop every mapping)")
    wanted = _files(repo)
    missing = [s for s in wanted.values() if not (repo / s).is_file()]
    if missing:
        sys.exit(f"not found in {repo}: {', '.join(missing)}")
    if not force and _git(repo, "status", "--porcelain", "--", *wanted.values()):
        sys.exit(
            f"Refusing to apply: uncommitted changes to the vendored sources in "
            f"{repo}. Commit there first, or re-run with --force."
        )
    (dest / "mappings").mkdir(parents=True, exist_ok=True)
    for stale in (dest / "mappings").glob("*.json"):
        if f"mappings/{stale.name}" not in wanted:
            stale.unlink()  # a mapping shot-aligner removed
    files = {}
    for name, source in wanted.items():
        shutil.copyfile(repo / source, dest / name)
        files[name] = {"from": source, "sha256": _sha256(dest / name)}
    record = {
        "commit": _git(repo, "log", "-1", "--format=%h", "--", *wanted.values()),
        "files": files,
        "repository": REPOSITORY,
        "source": (
            "shot-aligner readers, pack helpers, pack manifests, the "
            "instrument catalogue and the per-instrument mapping rows"
        ),
    }
    with (dest / MANIFEST).open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(f"  Applied {len(files)} file(s) from {repo} at {record['commit']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="copy and re-pin")
    parser.add_argument("--force", action="store_true", help="apply despite changes")
    parser.add_argument(
        "--shot-aligner",
        type=Path,
        default=None,
        help="shot-aligner checkout (default: ../shot-aligner, or $SHOT_ALIGNER_ROOT)",
    )
    args = parser.parse_args(argv)
    repo = args.shot_aligner or _default_repo()

    print("--- Pack code sync (shot-aligner -> hzdr_packs/vendor) ---")
    if args.apply:
        apply(repo, args.force)
        return 0
    problems = check(repo)
    for problem in problems:
        print(f"  DRIFT: {problem}")
    if problems:
        print(
            "Vendored pack code drift. To re-vendor: "
            "hzdr/scripts/sync-hzdr-packs.sh --apply "
            "(or sync-hzdr-packs.ps1 -Apply)"
        )
        return 1
    print("  Vendored pack code in sync.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
