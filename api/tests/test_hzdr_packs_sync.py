"""The vendored pack code is pinned, and its sync check catches drift.

`hzdr/scripts/sync_hzdr_packs.py` (behind `sync-hzdr-packs.{sh,ps1}`) keeps
`damnit_api/metadata/hzdr_packs/vendor/` byte-identical to shot-aligner's
readers, pack helpers and pack manifests. These tests run its check without the
sibling checkout: against the hashes in SOURCE.json, and against a fake
shot-aligner tree that drifts.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "hzdr" / "scripts" / "sync_hzdr_packs.py"


def _load():
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec = importlib.util.spec_from_file_location("sync_hzdr_packs", SCRIPT)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SCRIPT.parent))
    return module


sync = _load()


def test_source_json_pins_every_vendored_file():
    record = json.loads((sync.DEST / "SOURCE.json").read_text(encoding="utf-8"))
    files = sync._files(None, record)
    assert set(record["files"]) == set(files)
    assert set(sync.FILES) < set(files)  # plus the mapping rows (phase 4b)
    assert any(name.startswith("mappings/") for name in files)
    assert record["commit"]
    for name, entry in record["files"].items():
        assert entry["from"] == files[name]
        digest = hashlib.sha256((sync.DEST / name).read_bytes()).hexdigest()
        assert digest == entry["sha256"], name


def test_without_the_sibling_only_the_hashes_are_checked(tmp_path, capsys):
    assert sync.check(tmp_path / "no-shot-aligner") == []
    assert "Skipped the shot-aligner comparison" in capsys.readouterr().out


def _fake_shot_aligner(root: Path) -> Path:
    record = json.loads((sync.DEST / "SOURCE.json").read_text(encoding="utf-8"))
    for name, source in sync._files(None, record).items():
        target = root / source
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sync.DEST / name, target)
    return root


def test_an_identical_sibling_is_in_sync(tmp_path):
    assert sync.check(_fake_shot_aligner(tmp_path)) == []


def test_a_changed_source_and_a_changed_copy_are_both_drift(tmp_path):
    repo = _fake_shot_aligner(tmp_path / "repo")
    with (repo / sync.FILES["irr8.py"]).open("a", encoding="utf-8") as stream:
        stream.write("# changed upstream\n")
    dest = tmp_path / "vendor"
    shutil.copytree(sync.DEST, dest)
    with (dest / "img_csv.py").open("a", encoding="utf-8") as stream:
        stream.write("# edited by hand\n")
    problems = sync.check(repo, dest)
    assert "irr8.py: differs from shot-aligner's polina/irr8.py" in problems
    assert "img_csv.py: does not match its hash in SOURCE.json" in problems


def test_apply_copies_and_repins(tmp_path):
    repo = _fake_shot_aligner(tmp_path / "repo")
    with (repo / sync.FILES["nxwrite.py"]).open("a", encoding="utf-8") as stream:
        stream.write("# newer\n")
    dest = tmp_path / "vendor"
    shutil.copytree(sync.DEST, dest)
    sync.apply(repo, force=True, dest=dest)
    assert sync.check(repo, dest) == []
    record = json.loads((dest / "SOURCE.json").read_text(encoding="utf-8"))
    assert (
        record["files"]["nxwrite.py"]["sha256"]
        == hashlib.sha256((repo / sync.FILES["nxwrite.py"]).read_bytes()).hexdigest()
    )


def test_a_mapping_added_upstream_is_drift_and_apply_brings_it(tmp_path):
    repo = _fake_shot_aligner(tmp_path / "repo")
    (repo / sync._MAPPINGS / "New_Camera.json").write_text("{}", encoding="utf-8")
    dest = tmp_path / "vendor"
    shutil.copytree(sync.DEST, dest)
    assert any("New_Camera.json" in p for p in sync.check(repo, dest))
    sync.apply(repo, force=True, dest=dest)
    assert (dest / "mappings" / "New_Camera.json").is_file()
    assert sync.check(repo, dest) == []


def test_a_mapping_removed_upstream_is_removed_by_apply(tmp_path):
    repo = _fake_shot_aligner(tmp_path / "repo")
    (repo / sync._MAPPINGS / "BAM.json").unlink()
    dest = tmp_path / "vendor"
    shutil.copytree(sync.DEST, dest)
    assert sync.check(repo, dest)
    sync.apply(repo, force=True, dest=dest)
    assert not (dest / "mappings" / "BAM.json").exists()
    assert sync.check(repo, dest) == []


def test_a_stray_vendored_mapping_is_drift(tmp_path):
    dest = tmp_path / "vendor"
    shutil.copytree(sync.DEST, dest)
    (dest / "mappings" / "Stray.json").write_text("{}", encoding="utf-8")
    problems = sync.check(tmp_path / "no-shot-aligner", dest)
    assert any("Stray.json" in p for p in problems)


def test_apply_refuses_a_checkout_without_mappings(tmp_path):
    repo = _fake_shot_aligner(tmp_path / "repo")
    shutil.rmtree(repo / sync._MAPPINGS)
    dest = tmp_path / "vendor"
    shutil.copytree(sync.DEST, dest)
    with pytest.raises(SystemExit):
        sync.apply(repo, force=True, dest=dest)
    assert list((dest / "mappings").glob("*.json"))
