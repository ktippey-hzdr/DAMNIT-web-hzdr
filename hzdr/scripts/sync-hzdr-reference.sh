#!/usr/bin/env bash
# Check (default) or apply (--apply) the vendored copy of shot-aligner's
# reference fixture in api/tests/fixtures/hzdr-reference/, and re-pin its
# SOURCE.json. Sibling of sync-hzdr-event.ps1; the logic is in
# sync_hzdr_reference.py (stdlib only), shared with sync-hzdr-reference.ps1.
#
#   hzdr/scripts/sync-hzdr-reference.sh                 # check
#   hzdr/scripts/sync-hzdr-reference.sh --apply         # copy from ../shot-aligner
#   hzdr/scripts/sync-hzdr-reference.sh --apply --force # even with uncommitted changes there
#   SHOT_ALIGNER_ROOT=/path/to/shot-aligner hzdr/scripts/sync-hzdr-reference.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
fi
if command -v python3 >/dev/null 2>&1; then
    exec python3 "$here/sync_hzdr_reference.py" "$@"
elif command -v uv >/dev/null 2>&1; then
    exec uv run --no-project python "$here/sync_hzdr_reference.py" "$@"
else
    exec python "$here/sync_hzdr_reference.py" "$@"
fi
