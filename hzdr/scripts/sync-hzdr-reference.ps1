#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Check (or apply) the vendored copy of shot-aligner's reference fixture.

.DESCRIPTION
    shot-aligner owns shot_aligner/tests/fixtures/reference/ (raws, the
    hzdr-event-v1 events, the container manifest). DAMNIT-web-hzdr vendors it
    byte for byte into api/tests/fixtures/hzdr-reference/, with SOURCE.json
    recording the shot-aligner commit and every file's sha256.

    In Check mode (default): fails if the vendored files differ from
    ..\shot-aligner's, or from the hashes in SOURCE.json.
    In Apply mode: copies shot-aligner's files over and rewrites SOURCE.json.

    The logic is sync_hzdr_reference.py (stdlib only), shared with
    sync-hzdr-reference.sh.

.PARAMETER Apply
    Copy from shot-aligner and re-pin SOURCE.json instead of only checking.
    Refuses when the fixture has uncommitted changes in shot-aligner.

.PARAMETER Force
    With -Apply: copy even when shot-aligner's fixture has uncommitted changes.

.PARAMETER ShotAligner
    The shot-aligner checkout (default: ..\shot-aligner beside this repo, or
    $env:SHOT_ALIGNER_ROOT).

.EXAMPLE
    .\sync-hzdr-reference.ps1
    .\sync-hzdr-reference.ps1 -Apply
#>
param(
    [switch] $Apply,
    [switch] $Force,
    [string] $ShotAligner = ""
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path $MyInvocation.MyCommand.Path -Parent
$helper    = Join-Path $scriptDir "sync_hzdr_reference.py"

$pyArgs = @($helper)
if ($Apply)       { $pyArgs += "--apply" }
if ($Force)       { $pyArgs += "--force" }
if ($ShotAligner) { $pyArgs += @("--shot-aligner", $ShotAligner) }

if (Get-Command uv -ErrorAction SilentlyContinue) {
    & uv run --no-project python @pyArgs
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python @pyArgs
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 @pyArgs
} else {
    Write-Host "  Python not found (uv, python or py); cannot check the reference fixture." -ForegroundColor Red
    exit 1
}
# Explicit exit with the helper's status, so callers checking $LASTEXITCODE
# see drift as a failure.
exit $LASTEXITCODE
