"""Capture the five HZDR frontend documentation views from a local fixture stack.

Run from the repository root:

    uv run --group screenshots \
        python hzdr/scripts/capture-screenshots.py

The command creates canonical fixture events in a temporary directory, runs the
real package emulator, API, and Vite frontend on ephemeral localhost ports,
captures the five pages with headless Chromium, writes a SHA-256 receipt, and
stops every child process. No broker, database, or production service is used.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NamedTuple
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "hzdr" / "docs" / "screenshots"
RECEIPT_PATH = OUT_DIR / "capture-receipt.json"
VIEWPORT = {"width": 1600, "height": 900}


class Shot(NamedTuple):
    filename: str
    path: str
    ready_kind: str
    ready_value: str


SHOTS = (
    Shot("home.png", "/home", "heading", "DAMNIT! HZDR workspace"),
    Shot("shot-table.png", "/source/hzdr-emulator", "selector", "table"),
    Shot("flow-monitor.png", "/flow-monitor", "heading", "HZDR flow monitor"),
    Shot(
        "link-shot-records.png",
        "/link-shot-records",
        "heading",
        "Link Existing Shot Records",
    ),
    Shot("docs.png", "/docs", "heading", "DAMNIT-web HZDR workflow"),
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _fixture_events() -> tuple[dict[str, object], ...]:
    common = {
        "schema_version": "hzdr-event-v1",
        "experiment_id": "Pilot_Screenshot_Automation",
        "shot_id": "shot-000001",
        "shot_number": 1,
        "timestamp": "2026-07-01T10:00:00Z",
    }
    return (
        {
            **common,
            "event_id": "screenshot-laserdata-000001",
            "source": "LaserData",
            "kind": "pulse_energy_j",
            "transport": "asapo",
            "payload_ref": {
                "endpoint": "local-fixture",
                "stream": "laser",
                "message_id": 1,
            },
            "values": [12.4],
            "metadata": {
                "operator": "docs-capture",
                "laser": {"pulse_energy": 12.4, "pulse_duration": 30.0},
                "target": {
                    "type": "foil",
                    "name": "Cu target",
                    "provenance": "fixture",
                },
            },
        },
        {
            **common,
            "event_id": "screenshot-watchdog-000001",
            "source": "DAQ-File-Watchdog",
            "kind": "file_created",
            "transport": "kafka",
            "payload_ref": {
                "topic": "planet.watchdog.events",
                "partition": 0,
                "offset": 1,
                "path": "fixture/shot-000001.h5",
            },
            "metadata": {
                "watch_name": "docs-capture",
                "status": "processed",
                "xray_counts": 1467,
            },
        },
    )


def _write_fixture_events(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for index, event in enumerate(_fixture_events(), start=1):
        path = directory / f"event-{index}.json"
        path.write_text(
            json.dumps(event, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _process_options() -> dict[str, object]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _start_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        **_process_options(),
    )


def _wait_for_url(
    url: str,
    process: subprocess.Popen[bytes],
    *,
    timeout: int = 60,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Process exited with code {process.returncode} "
                f"before {url} became ready."
            )
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            time.sleep(0.15)
    raise TimeoutError(f"Timed out waiting for {url}.")


def _stop_process_tree(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        taskkill = shutil.which("taskkill")
        if taskkill:
            subprocess.run(
                [taskkill, "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        process.kill()
        process.wait(timeout=5)


def _frontend_command() -> list[str]:
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        raise SystemExit("pnpm is required to start the HZDR frontend.")
    arguments = [
        pnpm,
        "--filter",
        "@damnit-frontend/app",
        "dev",
    ]
    if os.name != "nt":
        return arguments
    command_shell = shutil.which("cmd")
    if command_shell is None:
        raise SystemExit("cmd.exe is required to start pnpm on Windows.")
    return [command_shell, "/d", "/c", *arguments]


def _write_receipt() -> None:
    receipt = {
        "schemaVersion": 1,
        "capturedAtUtc": datetime.now(UTC).isoformat(),
        "files": {
            shot.filename: hashlib.sha256(
                (OUT_DIR / shot.filename).read_bytes()
            ).hexdigest()
            for shot in SHOTS
        },
    }
    RECEIPT_PATH.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def capture() -> None:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "Playwright is required. Run this command through "
            "`uv run --group screenshots`."
        ) from exc

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    api_process: subprocess.Popen[bytes] | None = None
    frontend_process: subprocess.Popen[bytes] | None = None

    with TemporaryDirectory(prefix="damnit-doc-capture-") as temp_dir:
        temp_root = Path(temp_dir)
        events_dir = temp_root / "events"
        emulator_dir = temp_root / "emulator"
        _write_fixture_events(events_dir)

        subprocess.run(
            [
                sys.executable,
                str(ROOT / "api" / "scripts" / "hzdr-package-emulator.py"),
                "--events-dir",
                str(events_dir),
                "--output-dir",
                str(emulator_dir),
                "--source-key",
                "hzdr-emulator",
                "--shot-count",
                "6",
            ],
            cwd=ROOT / "api",
            check=True,
        )

        api_port = _free_port()
        frontend_port = _free_port()
        api_url = f"http://127.0.0.1:{api_port}"
        frontend_url = f"http://127.0.0.1:{frontend_port}"
        api_env = os.environ.copy()
        api_env.update(
            {
                "DW_API_AUTH__MODE": "none",
                "DW_API_DEBUG": "false",
                "DW_API_LOG_LEVEL": "WARNING",
                "DW_API_METADATA__PROVIDER": "local",
                "DW_API_METADATA__SOURCES_FILE": str(
                    emulator_dir / "hzdr_sources.json"
                ),
                "DW_API_UVICORN__HOST": "127.0.0.1",
                "DW_API_UVICORN__PORT": str(api_port),
                "DW_API_UVICORN__RELOAD": "false",
            }
        )
        frontend_env = os.environ.copy()
        frontend_env.update(
            {
                "VITE_API": api_url,
                "VITE_PORT": str(frontend_port),
            }
        )

        try:
            api_process = _start_process(
                [sys.executable, "-m", "damnit_api.main"],
                cwd=ROOT / "api",
                env=api_env,
            )
            _wait_for_url(f"{api_url}/config/runtime", api_process)

            frontend_process = _start_process(
                _frontend_command(),
                cwd=ROOT / "frontend",
                env=frontend_env,
            )
            _wait_for_url(f"{frontend_url}/home", frontend_process)

            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True)
                except PlaywrightError as exc:
                    raise SystemExit(
                        "Playwright Chromium is not installed. Run "
                        "`uv run --group screenshots playwright install chromium` "
                        "once, then retry."
                    ) from exc
                try:
                    page = browser.new_page(viewport=VIEWPORT)
                    page.emulate_media(color_scheme="light", reduced_motion="reduce")
                    for shot in SHOTS:
                        page.goto(
                            f"{frontend_url}{shot.path}",
                            wait_until="networkidle",
                        )
                        if shot.ready_kind == "heading":
                            page.get_by_role(
                                "heading",
                                name=shot.ready_value,
                                exact=True,
                            ).wait_for()
                        else:
                            page.locator(shot.ready_value).first.wait_for()
                        page.screenshot(
                            path=OUT_DIR / shot.filename,
                            full_page=True,
                        )
                        print(f"wrote {OUT_DIR / shot.filename}")
                finally:
                    browser.close()
            _write_receipt()
            print(f"wrote {RECEIPT_PATH}")
        finally:
            _stop_process_tree(frontend_process)
            _stop_process_tree(api_process)


def main() -> None:
    capture()


if __name__ == "__main__":
    main()
