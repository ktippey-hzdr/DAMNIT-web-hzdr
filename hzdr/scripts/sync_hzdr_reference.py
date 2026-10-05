"""Check (or apply) the vendored copy of shot-aligner's reference fixture.

The one implementation behind ``sync-hzdr-reference.ps1`` and
``sync-hzdr-reference.sh``; stdlib only.

shot-aligner owns ``shot_aligner/tests/fixtures/reference/`` (raws, events,
manifest, README). DAMNIT-web-hzdr vendors it byte for byte into
``api/tests/fixtures/hzdr-reference/`` and records, in ``SOURCE.json``, the
shot-aligner commit it was copied from and every file's sha256.

Check (default): fails when the vendored files differ from shot-aligner's
(added, removed or changed) or from the hashes in SOURCE.json. Without the
sibling checkout only the SOURCE.json hashes are checked.

Apply (--apply): replaces the vendored files with shot-aligner's and rewrites
SOURCE.json. Refuses when the fixture has uncommitted changes in shot-aligner
(its commit would not describe what was copied) unless --force.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess  # noqa: S404 -- git only, fixed arguments
import sys
from pathlib import Path

DAMNIT_ROOT = Path(__file__).resolve().parents[2]
DEST = DAMNIT_ROOT / "api" / "tests" / "fixtures" / "hzdr-reference"
FIXTURE_IN_REPO = "shot_aligner/tests/fixtures/reference"
REPOSITORY = "https://codebase.helmholtz.cloud/tippey27/shot-aligner"
MANIFEST = "SOURCE.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _files(root: Path) -> dict[str, Path]:
    return {
        p.relative_to(root).as_posix(): p
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name != MANIFEST and "__pycache__" not in p.parts
    }


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _default_repo() -> Path:
    env = os.environ.get("SHOT_ALIGNER_ROOT")
    return Path(env) if env else DAMNIT_ROOT.parent / "shot-aligner"


def check(repo: Path) -> list[str]:
    problems = []
    recorded = json.loads((DEST / MANIFEST).read_text(encoding="utf-8"))
    vendored = _files(DEST)
    if set(vendored) != set(recorded["files"]):
        problems.append(
            f"{MANIFEST} lists {sorted(set(recorded['files']) ^ set(vendored))} "
            "differently from the vendored folder"
        )
    for name, path in vendored.items():
        if name in recorded["files"] and _sha256(path) != recorded["files"][name]:
            problems.append(f"{name}: does not match its hash in {MANIFEST}")

    source = repo / FIXTURE_IN_REPO
    if not source.is_dir():
        print(f"  Skipped the shot-aligner comparison (not found at {source})")
        return problems
    upstream = _files(source)
    problems += [
        f"{name}: in shot-aligner, not vendored"
        for name in sorted(set(upstream) - set(vendored))
    ]
    problems += [
        f"{name}: vendored, no longer in shot-aligner"
        for name in sorted(set(vendored) - set(upstream))
    ]
    problems += [
        f"{name}: differs from shot-aligner's"
        for name in sorted(set(upstream) & set(vendored))
        if upstream[name].read_bytes() != vendored[name].read_bytes()
    ]
    head = _git(repo, "log", "-1", "--format=%h", "--", FIXTURE_IN_REPO)
    if not problems and head and head != recorded.get("commit"):
        print(
            f"  Note: files agree, but {MANIFEST} names commit "
            f"{recorded.get('commit')} and shot-aligner's fixture is at {head}; "
            "--apply re-pins it."
        )
    return problems


def apply(repo: Path, force: bool) -> None:
    source = repo / FIXTURE_IN_REPO
    if not source.is_dir():
        sys.exit(f"shot-aligner's fixture not found at {source}")
    if not force and _git(repo, "status", "--porcelain", "--", FIXTURE_IN_REPO):
        sys.exit(
            f"Refusing to apply: uncommitted changes under {FIXTURE_IN_REPO} in "
            f"{repo}. Commit there first, or re-run with --force."
        )
    for path in _files(DEST).values():
        path.unlink()
    for directory in sorted(
        (p for p in DEST.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)
    ):
        if not any(directory.iterdir()):
            directory.rmdir()
    files = {}
    for name, path in _files(source).items():
        target = DEST / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        files[name] = _sha256(target)
    record = {
        "commit": _git(repo, "log", "-1", "--format=%h", "--", FIXTURE_IN_REPO),
        "files": files,
        "repository": REPOSITORY,
        "source": f"shot-aligner {FIXTURE_IN_REPO}",
    }
    with (DEST / MANIFEST).open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(f"  Applied {len(files)} file(s) from {source} at {record['commit']}")


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

    print("--- Reference fixture sync (shot-aligner -> hzdr-reference) ---")
    if args.apply:
        apply(repo, args.force)
        return 0
    problems = check(repo)
    for problem in problems:
        print(f"  DRIFT: {problem}")
    if problems:
        print(
            "Reference fixture drift. To re-vendor: "
            "hzdr/scripts/sync-hzdr-reference.sh --apply "
            "(or sync-hzdr-reference.ps1 -Apply)"
        )
        return 1
    print("  Vendored reference fixture in sync.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
