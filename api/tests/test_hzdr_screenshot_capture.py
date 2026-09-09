import importlib.util
import os
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parents[2] / "hzdr" / "scripts" / "capture-screenshots.py"
SPEC = importlib.util.spec_from_file_location("hzdr_screenshot_capture", SCRIPT_PATH)
assert SPEC is not None
capture = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = capture
SPEC.loader.exec_module(capture)


@pytest.fixture
def toolchain(tmp_path, monkeypatch):
    (tmp_path / ".nvmrc").write_text("24\n")
    monkeypatch.setattr(capture, "ROOT", tmp_path)
    monkeypatch.setenv("NVM_DIR", str(tmp_path / "nvm"))
    monkeypatch.setenv("PATH", "/system/bin")
    monkeypatch.setattr(capture.shutil, "which", lambda name: "/system/bin/node")
    monkeypatch.setattr(
        capture.subprocess, "check_output", lambda *args, **kwargs: "v18.16.0\n"
    )
    return tmp_path / "nvm" / "versions" / "node"


def test_compatible_node_preserves_path(toolchain, monkeypatch):
    monkeypatch.setattr(
        capture.subprocess, "check_output", lambda *args, **kwargs: "v24.16.0\n"
    )
    assert capture._frontend_environment()["PATH"] == "/system/bin"


def test_nvm_fallback_uses_latest_matching_version_only_in_child(toolchain):
    for version in ("v24.9.0", "v24.16.0", "v25.0.0"):
        binary = toolchain / version / "bin" / "node"
        binary.parent.mkdir(parents=True)
        binary.touch()
    env = capture._frontend_environment()
    assert env["PATH"].split(os.pathsep)[0] == str(toolchain / "v24.16.0" / "bin")
    assert os.environ["PATH"] == "/system/bin"


def test_missing_compatible_node_fails_before_starting_stack(toolchain):
    with pytest.raises(SystemExit, match="Node >= 24 is required"):
        capture._frontend_environment()
