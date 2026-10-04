#!/usr/bin/env bash
# Check (default) or apply (--apply) the vendored copy of shot-aligner's
# readers, pack helpers and pack manifests in
# api/src/damnit_api/metadata/hzdr_packs/vendor/, and re-pin its SOURCE.json.
# Sibling of sync-hzdr-reference.sh; the logic is in
# sync_hzdr_packs.py (stdlib only), shared with sync-hzdr-packs.ps1.
#
#   hzdr/scripts/sync-hzdr-packs.sh                 # check
#   hzdr/scripts/sync-hzdr-packs.sh --apply         # copy from ../shot-aligner
#   hzdr/scripts/sync-hzdr-packs.sh --apply --force # even with uncommitted changes there
#   SHOT_ALIGNER_ROOT=/path/to/shot-aligner hzdr/scripts/sync-hzdr-packs.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
fi
if command -v python3 >/dev/null 2>&1; then
    exec python3 "$here/sync_hzdr_packs.py" "$@"
elif command -v uv >/dev/null 2>&1; then
    exec uv run --no-project python "$here/sync_hzdr_packs.py" "$@"
else
    exec python "$here/sync_hzdr_packs.py" "$@"
fi
