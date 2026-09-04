"""Resolve a reviewed openPMD projection plan against a canonical NeXus file.

Reports which projection rules a projector could satisfy and why not for the
rest. Writes no openPMD output - see Phase 3 of
`hzdr/docs/plans/openpmd-projection-plan.md`.

    uv run python scripts/hzdr-openpmd-preflight.py \
        --nexus build/openpmd-fixture/canonical-openpmd-synth.nxs \
        --plan  build/openpmd-fixture/openpmd-projection-plan.json

Exit code 0 when the report status is `pass`, 1 when it is `fail`.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("DW_API_DAMNIT_PATH", str(Path.cwd()))

from damnit_api.metadata.hzdr_openpmd import (
    load_projection_plan,
    preflight_projection,
)


def _print_summary(report: dict) -> None:
    counts = report["counts"]
    print(f"status: {report['status']}")
    print(f"plan:   {report['plan']['title']}")
    print(
        f"openPMD standard {report['plan']['openpmd_standard']} | "
        f"{report['iteration']['shot_count']} iterations"
    )
    print(
        f"rules:  {counts['accepted']} accepted, "
        f"{counts['deferred']} deferred, {counts['rejected']} rejected"
    )
    policy = report["payload_policy"]
    print(
        f"payload: max {policy['max_resolve_bytes']} B | "
        f"oversize={policy['on_oversize']} pending={policy['on_pending']}"
    )
    for issue in report["plan_issues"] + report["iteration"]["issues"]:
        print(f"  [{issue['severity']}] {issue['code']}: {issue['detail']}")
    for rule in report["rules"]:
        print(f"  {rule['status']:>8}  {rule['target_name']} <- {rule['source_path']}")
        for issue in rule["issues"]:
            print(
                f"            [{issue['severity']}] {issue['code']}: {issue['detail']}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nexus", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument(
        "--report",
        type=Path,
        help="Write the full JSON report here as well as summarizing it.",
    )
    args = parser.parse_args()

    plan = load_projection_plan(args.plan)
    report = preflight_projection(nexus_path=args.nexus, plan=plan)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    _print_summary(report)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
