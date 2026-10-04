"""Convert each shot's raw files into its NeXus container (campaign output phase 3).

    python api/scripts/hzdr-container-worker.py --master <campaign>.nxs
    python api/scripts/hzdr-container-worker.py --output-root <root>

Reads the published master(s), writes ``<campaign folder>/shots/<YYYYMMDD>_
<shot_number>.nxs`` for every shot whose inputs changed, and leaves the rest.
Runs outside the builder's campaign lock, under its own
(``shots/.convert.lock``); a second worker started meanwhile leaves a request
for the running one and exits. Started by the builder auto-trigger when
``DW_API_HZDR_BUILDER__CONTAINERS_ENABLED=true``; safe to run by hand. Raw
files are read through ``DW_API_METADATA__PATH_MAP`` unless ``--path-map`` is
given. Design: ``hzdr/docs/plans/container-writer.md``.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

os.environ.setdefault("DW_API_DAMNIT_PATH", str(Path.cwd()))

from damnit_api.metadata.hzdr_containers import (
    campaign_masters,
    make_read_path,
    run_conversion,
)


def _configured_path_map() -> str:
    """``DW_API_METADATA__PATH_MAP``, read the builder's lazy way."""
    from damnit_api.shared.settings import settings

    return settings.metadata.path_map


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert each shot's raw files into its NeXus container."
    )
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument(
        "--master",
        action="append",
        type=Path,
        help="A campaign's published master (<campaign>.nxs); repeatable.",
    )
    where.add_argument(
        "--output-root",
        type=Path,
        help="Multi-campaign output root: every <root>/<campaign>/<campaign>.nxs.",
    )
    parser.add_argument(
        "--path-map",
        default=None,
        help="'from=to' prefixes, comma separated (default: "
        "DW_API_METADATA__PATH_MAP).",
    )
    args = parser.parse_args(argv)

    path_map = args.path_map if args.path_map is not None else _configured_path_map()
    read_path = make_read_path(path_map)
    masters = args.master or campaign_masters(args.output_root)

    failures = 0
    for master in masters:
        if not master.is_file():
            print(f"Containers ({master.name}): no published master yet")
            continue
        try:
            runs = run_conversion(master.resolve(), read_path=read_path)
        except Exception:
            failures += 1
            print(f"Container conversion failed for {master}:", file=sys.stderr)
            traceback.print_exc()
            continue
        if not runs:
            print(f"Containers ({master.name}): another worker is converting; asked it")
            continue
        written = sum(len(run.written) for run in runs)
        current = len(runs[-1].skipped)
        problems = sum(run.problems for run in runs)
        print(
            f"Containers ({master.name}): {written} written, {current} up to date, "
            f"{problems} problem(s) recorded, {len(runs)} pass(es)"
        )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
