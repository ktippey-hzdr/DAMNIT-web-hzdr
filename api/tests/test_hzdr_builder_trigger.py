# The fake processes stand in for asyncio.subprocess.Process, which only
# their wait() and returncode are asked for.
# pyright: reportArgumentType=false
"""Tests for the debounced builder auto-trigger (consumer/builder_trigger.py).

The trigger coalesces spool events into subprocess reruns of the builder.  These
tests inject a fake runner so no real builder subprocess is spawned, and drive
the debounce loop with a short window for speed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from damnit_api.consumer.builder_trigger import BuilderTrigger
from damnit_api.consumer.spool import HZDRSpoolConsumer, SpoolConfig
from damnit_api.shared.hzdr_settings import HZDRBuilderSettings

DEBOUNCE = 0.05


def _settings(tmp_path: Path, **overrides) -> HZDRBuilderSettings:
    base = {
        "enabled": True,
        "debounce_seconds": DEBOUNCE,
        "output_nexus": tmp_path / "campaign.nxs",
    }
    base.update(overrides)
    return HZDRBuilderSettings(**base)


class _RecordingRunner:
    """Fake builder runner: counts calls, optionally simulates build time."""

    def __init__(self, build_time: float = 0.0, returncode: int = 0) -> None:
        self.calls: list[list[str]] = []
        self._build_time = build_time
        self._returncode = returncode

    async def __call__(self, cmd):
        self.calls.append(list(cmd))
        if self._build_time:
            await asyncio.sleep(self._build_time)
        return self._returncode, ""


async def _drive(trigger: BuilderTrigger, stop: asyncio.Event) -> asyncio.Task:
    task = asyncio.create_task(trigger.run(stop))
    await asyncio.sleep(0)  # let the loop reach its first wait
    return task


async def _shutdown(task: asyncio.Task, stop: asyncio.Event, trigger: BuilderTrigger):
    stop.set()
    trigger.notify()  # unblock the wait-for-wake race
    await asyncio.wait_for(task, timeout=1.0)


# ---------------------------------------------------------------------------
# Command assembly
# ---------------------------------------------------------------------------


def test_build_command_includes_spool_paths_and_settings(tmp_path):
    settings = _settings(
        tmp_path,
        experiment_id="EXP-1",
        source_key="hzdr-labfrog",
        campaign_timezone="Europe/Berlin",
        labfrog_sqlite=tmp_path / "c.sqlite",
        match_tolerance_s=90.0,
        extra_args=["--verbose"],
    )
    trigger = BuilderTrigger(
        settings,
        events_jsonl=[tmp_path / "spool/events.jsonl"],
        trigger_jsonl=[tmp_path / "spool/trigger.jsonl"],
    )
    cmd = trigger.build_command()

    assert cmd[1].endswith("hzdr-hdf5-builder.py")
    assert "--events-jsonl" in cmd
    assert str(tmp_path / "spool/events.jsonl") in cmd
    assert "--trigger-jsonl" in cmd
    assert str(tmp_path / "spool/trigger.jsonl") in cmd
    assert cmd[cmd.index("--output-nexus") + 1] == str(tmp_path / "campaign.nxs")
    assert cmd[cmd.index("--experiment-id") + 1] == "EXP-1"
    assert cmd[cmd.index("--campaign-timezone") + 1] == "Europe/Berlin"
    assert cmd[cmd.index("--labfrog-sqlite") + 1] == str(tmp_path / "c.sqlite")
    assert cmd[cmd.index("--match-tolerance-s") + 1] == "90.0"
    assert cmd[-1] == "--verbose"


def test_build_command_omits_unset_optional_inputs(tmp_path):
    trigger = BuilderTrigger(_settings(tmp_path))
    cmd = trigger.build_command()
    assert "--experiment-id" not in cmd  # empty string -> omitted
    assert "--labfrog-nexus" not in cmd
    assert "--labfrog-sqlite" not in cmd
    assert "--sources-file" not in cmd


# ---------------------------------------------------------------------------
# Debounce / coalescing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_burst_of_events_coalesces_to_one_build(tmp_path):
    runner = _RecordingRunner(build_time=2 * DEBOUNCE)
    trigger = BuilderTrigger(_settings(tmp_path), runner=runner)
    stop = asyncio.Event()
    task = await _drive(trigger, stop)

    for _ in range(5):
        trigger.notify()

    await asyncio.sleep(6 * DEBOUNCE)
    assert len(runner.calls) == 1, "burst should coalesce into a single build"

    await _shutdown(task, stop, trigger)


@pytest.mark.asyncio
async def test_events_after_build_rearm_a_second_build(tmp_path):
    runner = _RecordingRunner()
    trigger = BuilderTrigger(_settings(tmp_path), runner=runner)
    stop = asyncio.Event()
    task = await _drive(trigger, stop)

    trigger.notify()
    await asyncio.sleep(4 * DEBOUNCE)
    assert len(runner.calls) == 1

    trigger.notify()
    await asyncio.sleep(4 * DEBOUNCE)
    assert len(runner.calls) == 2

    await _shutdown(task, stop, trigger)


@pytest.mark.asyncio
async def test_idle_trigger_never_builds(tmp_path):
    runner = _RecordingRunner()
    trigger = BuilderTrigger(_settings(tmp_path), runner=runner)
    stop = asyncio.Event()
    task = await _drive(trigger, stop)

    await asyncio.sleep(4 * DEBOUNCE)
    assert runner.calls == []

    await _shutdown(task, stop, trigger)


@pytest.mark.asyncio
async def test_stop_exits_cleanly_without_building(tmp_path):
    runner = _RecordingRunner()
    trigger = BuilderTrigger(_settings(tmp_path), runner=runner)
    stop = asyncio.Event()
    task = await _drive(trigger, stop)

    stop.set()
    trigger.notify()
    await asyncio.wait_for(task, timeout=1.0)
    assert runner.calls == []


@pytest.mark.asyncio
async def test_builder_failure_does_not_crash_loop(tmp_path):
    runner = _RecordingRunner(returncode=1)
    trigger = BuilderTrigger(_settings(tmp_path), runner=runner)
    stop = asyncio.Event()
    task = await _drive(trigger, stop)

    trigger.notify()
    await asyncio.sleep(4 * DEBOUNCE)
    # Loop survives a failing build and remains ready to rebuild.
    trigger.notify()
    await asyncio.sleep(4 * DEBOUNCE)
    assert len(runner.calls) == 2

    await _shutdown(task, stop, trigger)


# ---------------------------------------------------------------------------
# Consumer hook dispatch
# ---------------------------------------------------------------------------


class _StubConsumer(HZDRSpoolConsumer):
    async def _claim(self):  # pragma: no cover - not exercised
        return [], None

    async def _ack(self, token):  # pragma: no cover - not exercised
        return None


def test_on_new_events_dispatches_to_hook(tmp_path):
    consumer = _StubConsumer(SpoolConfig("camp", "grp", tmp_path))
    seen: list[list[Path]] = []
    consumer.on_new_events_hook = seen.append

    consumer.on_new_events([tmp_path / "events.jsonl"])
    assert seen == [[tmp_path / "events.jsonl"]]


def test_on_new_events_without_hook_is_noop(tmp_path):
    consumer = _StubConsumer(SpoolConfig("camp", "grp", tmp_path))
    # Must not raise when no hook is attached (auto-trigger disabled).
    consumer.on_new_events([tmp_path / "events.jsonl"])


# ---------------------------------------------------------------------------
# Settings validation
# ---------------------------------------------------------------------------


def test_enabled_requires_output_nexus():
    with pytest.raises(ValueError, match="OUTPUT_NEXUS"):
        HZDRBuilderSettings(enabled=True)


def test_disabled_allows_missing_output_nexus():
    settings = HZDRBuilderSettings(enabled=False)
    assert settings.output_nexus is None


# ---------------------------------------------------------------------------
# Concurrency / shutdown edge cases (merged from the alternate design)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_events_during_a_running_build_rearm_exactly_one_followup(tmp_path):
    """Events landing *while a build runs* must coalesce into one follow-up.

    Unlike the sequential re-arm test, this blocks the runner mid-build to
    exercise the concurrent case: many notifies during a single build must
    schedule exactly one more build, never one per notify.
    """
    calls: list[list[str]] = []
    first_started = asyncio.Event()
    release = asyncio.Event()

    async def runner(cmd):
        idx = len(calls)
        calls.append(list(cmd))
        if idx == 0:
            first_started.set()
            await release.wait()  # hold the first build open
        return 0, ""

    trigger = BuilderTrigger(_settings(tmp_path), runner=runner)
    stop = asyncio.Event()
    task = await _drive(trigger, stop)

    trigger.notify()
    await asyncio.wait_for(first_started.wait(), timeout=1.0)

    # A burst of notifies while the first build is blocked must collapse to one.
    for _ in range(5):
        trigger.notify()
    release.set()

    await asyncio.sleep(6 * DEBOUNCE)
    assert len(calls) == 2, f"expected exactly one follow-up run, got {len(calls)}"

    await _shutdown(task, stop, trigger)


@pytest.mark.asyncio
async def test_runner_exception_is_isolated_and_loop_survives(tmp_path):
    """A runner that *raises* (launch failure) is logged, not fatal.

    Branch A's returncode!=0 test covers a builder that exits non-zero; this
    covers the harder path where the runner itself raises before returning.
    """
    calls: list[list[str]] = []

    async def runner(cmd):  # noqa: RUF029 - coroutine runner contract
        calls.append(list(cmd))
        if len(calls) == 1:
            msg = "boom"
            raise RuntimeError(msg)
        return 0, ""

    trigger = BuilderTrigger(_settings(tmp_path), runner=runner)
    stop = asyncio.Event()
    task = await _drive(trigger, stop)

    trigger.notify()
    await asyncio.sleep(4 * DEBOUNCE)
    # The worker survived the exception and is ready to build again.
    trigger.notify()
    await asyncio.sleep(4 * DEBOUNCE)
    assert len(calls) == 2

    await _shutdown(task, stop, trigger)


@pytest.mark.asyncio
async def test_cancel_during_inflight_build_stops_promptly(tmp_path):
    """The main.py shutdown path (task.cancel) must not hang on a live build.

    A build in progress is interrupted by cancelling the run() task — the same
    thing the FastAPI lifespan does on shutdown — and must unwind promptly
    rather than waiting for the (never-completing) runner.
    """
    started = asyncio.Event()
    release = asyncio.Event()

    async def runner(cmd):
        started.set()
        await release.wait()  # never released; cancel must unwind this
        return 0, ""

    trigger = BuilderTrigger(_settings(tmp_path), runner=runner)
    stop = asyncio.Event()
    task = await _drive(trigger, stop)

    trigger.notify()
    await asyncio.wait_for(started.wait(), timeout=1.0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1.0)


# ---------------------------------------------------------------------------
# Default subprocess runner (real, trivial commands — no builder needed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subprocess_runner_reports_success(tmp_path):
    trigger = BuilderTrigger(_settings(tmp_path))
    rc, output = await trigger._run_subprocess([
        sys.executable,
        "-c",
        "import sys; sys.exit(0)",
    ])
    assert rc == 0
    assert output == ""


@pytest.mark.asyncio
async def test_subprocess_runner_reports_failure_with_output(tmp_path):
    trigger = BuilderTrigger(_settings(tmp_path))
    rc, output = await trigger._run_subprocess([
        sys.executable,
        "-c",
        "print('nope'); import sys; sys.exit(3)",
    ])
    assert rc == 3
    assert "nope" in output


@pytest.mark.asyncio
async def test_run_builder_once_logs_nonzero_exit(tmp_path, caplog):
    async def runner(cmd):  # noqa: RUF029 - coroutine runner contract
        return 7, "explosion in the build"

    trigger = BuilderTrigger(_settings(tmp_path), runner=runner)
    with caplog.at_level("ERROR"):
        await trigger._run_builder_once()
    assert any(
        "builder exited 7" in record.message and "explosion" in record.message
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# Shot containers (campaign output phase 3): the worker, started around a build
# ---------------------------------------------------------------------------


class _RecordingLauncher:
    """Fake container-worker launcher: records each start, in build order."""

    def __init__(self, log: list[str]) -> None:
        self.calls: list[list[str]] = []
        self._log = log

    async def __call__(self, cmd):
        self.calls.append(list(cmd))
        self._log.append("worker")


def _ordered_runner(log: list[str], returncode: int = 0):
    async def runner(cmd):  # noqa: RUF029 - coroutine runner contract
        log.append("build")
        return returncode, ""

    return runner


def test_containers_are_off_by_default(tmp_path):
    assert HZDRBuilderSettings().containers_enabled is False
    assert _settings(tmp_path).containers_enabled is False


@pytest.mark.asyncio
async def test_no_worker_is_started_unless_containers_are_enabled(tmp_path):
    log: list[str] = []
    launcher = _RecordingLauncher(log)
    trigger = BuilderTrigger(
        _settings(tmp_path), runner=_ordered_runner(log), worker_launcher=launcher
    )
    await trigger._run_builder_once()
    assert log == ["build"]


@pytest.mark.asyncio
async def test_the_worker_starts_before_and_after_a_build(tmp_path):
    log: list[str] = []
    launcher = _RecordingLauncher(log)
    trigger = BuilderTrigger(
        _settings(tmp_path, containers_enabled=True),
        runner=_ordered_runner(log),
        worker_launcher=launcher,
    )
    await trigger._run_builder_once()
    # Before: what is already published converts while the build runs.
    # After: the shots this build published, without waiting for an event.
    assert log == ["worker", "build", "worker"]
    assert launcher.calls[0] == launcher.calls[1] == trigger.worker_command()


@pytest.mark.asyncio
async def test_a_failed_build_starts_no_second_worker(tmp_path):
    log: list[str] = []
    trigger = BuilderTrigger(
        _settings(tmp_path, containers_enabled=True),
        runner=_ordered_runner(log, returncode=1),
        worker_launcher=_RecordingLauncher(log),
    )
    await trigger._run_builder_once()
    assert log == ["worker", "build"]


def test_worker_command_names_the_campaign_master(tmp_path):
    trigger = BuilderTrigger(_settings(tmp_path, containers_enabled=True))
    cmd = trigger.worker_command()
    assert cmd[0] == sys.executable
    assert cmd[1].endswith("hzdr-container-worker.py")
    assert Path(cmd[1]).is_file()
    assert cmd[2:] == ["--master", str(tmp_path / "campaign.nxs")]


def test_worker_command_covers_every_campaign_in_multi_campaign_mode(tmp_path):
    settings = HZDRBuilderSettings(
        enabled=True, output_root=tmp_path / "out", containers_enabled=True
    )
    cmd = BuilderTrigger(settings).worker_command()
    assert cmd[2:] == ["--output-root", str(tmp_path / "out")]


@pytest.mark.asyncio
async def test_the_default_launcher_does_not_wait_for_the_worker(tmp_path):
    trigger = BuilderTrigger(_settings(tmp_path, containers_enabled=True))
    marker = tmp_path / "done"
    await asyncio.wait_for(
        trigger._spawn_worker([
            sys.executable,
            "-c",
            (
                "import time, pathlib, sys; time.sleep(0.3); "
                f"pathlib.Path({str(marker)!r}).touch(); print('converted')"
            ),
        ]),
        timeout=0.2,
    )
    assert not marker.exists()  # still running: the build is not held up
    await trigger.wait_for_workers()
    assert marker.exists()
    log = tmp_path / ".hzdr-container-worker.log"
    assert "converted" in log.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_a_worker_that_cannot_start_does_not_stop_the_build(tmp_path, caplog):
    log: list[str] = []

    async def broken(cmd):  # noqa: RUF029 - launcher contract
        message = "no python"
        raise OSError(message)

    trigger = BuilderTrigger(
        _settings(tmp_path, containers_enabled=True),
        runner=_ordered_runner(log),
        worker_launcher=broken,
    )
    with caplog.at_level("ERROR"):
        await trigger._run_builder_once()
    assert log == ["build"]
    assert any("container worker" in r.message for r in caplog.records)


def test_worker_command_includes_the_bucket_only_when_configured(tmp_path):
    base = {
        "enabled": True,
        "output_root": tmp_path / "out",
        "containers_enabled": True,
    }
    assert (
        "--include-unassigned"
        not in BuilderTrigger(HZDRBuilderSettings(**base)).worker_command()
    )
    cmd = BuilderTrigger(
        HZDRBuilderSettings(**base, containers_include_unassigned=True)
    ).worker_command()
    assert cmd[-1] == "--include-unassigned"


@pytest.mark.asyncio
async def test_the_worker_log_is_rotated_when_it_grows(tmp_path, monkeypatch):
    from damnit_api.consumer import builder_trigger

    monkeypatch.setattr(builder_trigger, "WORKER_LOG_MAX_BYTES", 100)
    trigger = BuilderTrigger(_settings(tmp_path, containers_enabled=True))
    log = tmp_path / builder_trigger.WORKER_LOG_NAME
    log.write_text("x" * 200, encoding="utf-8")
    await trigger._spawn_worker([sys.executable, "-c", "print('fresh')"])
    await trigger.wait_for_workers()
    assert (tmp_path / (builder_trigger.WORKER_LOG_NAME + ".1")).read_text(
        encoding="utf-8"
    ) == "x" * 200
    assert log.read_text(encoding="utf-8").strip() == "fresh"


# --- Phase 4: a worker that left unlinked containers asks for one more build --


def test_the_relink_exit_matches_the_workers():
    from damnit_api.consumer import builder_trigger
    from damnit_api.metadata import hzdr_containers

    assert builder_trigger.RELINK_EXIT == hzdr_containers.RELINK_EXIT


@pytest.mark.parametrize(
    ("returncode", "rebuilds"), [(0, False), (1, False), (3, True), (4, True)]
)
def test_a_worker_that_left_containers_to_link_asks_for_a_build(
    tmp_path, returncode, rebuilds
):
    trigger = BuilderTrigger(_settings(tmp_path))
    trigger.worker_finished(returncode)
    assert trigger._wake.is_set() is rebuilds


# --- Phase 5: the NeXus validation gate after each build's worker -------------


class _Proc:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode

    async def wait(self) -> int:
        return self.returncode


def test_validation_is_off_by_default(tmp_path):
    assert HZDRBuilderSettings().validation_python == ""


@pytest.mark.asyncio
async def test_without_containers_validation_follows_the_build(tmp_path):
    log: list[str] = []
    validations = _RecordingLauncher(log)
    trigger = BuilderTrigger(
        _settings(tmp_path, validation_python="/nds/.venv/bin/python"),
        runner=_ordered_runner(log),
        validation_launcher=validations,
    )
    await trigger._run_builder_once()
    assert len(validations.calls) == 1
    cmd = validations.calls[0]
    assert cmd[0] == "/nds/.venv/bin/python"
    assert cmd[1].endswith("hzdr-nexus-validate.py")
    assert cmd[2:] == ["--master", str(tmp_path / "campaign.nxs")]


@pytest.mark.asyncio
async def test_with_containers_validation_follows_the_worker(tmp_path):
    log: list[str] = []
    validations = _RecordingLauncher([])
    trigger = BuilderTrigger(
        _settings(tmp_path, containers_enabled=True, validation_python="/nds/python"),
        runner=_ordered_runner(log),
        worker_launcher=_RecordingLauncher(log),
        validation_launcher=validations,
    )
    await trigger._run_builder_once()
    assert validations.calls == []  # not before the containers are done
    await trigger._reap_worker(_Proc(0))
    assert validations.calls == [trigger.validation_command()]


@pytest.mark.asyncio
async def test_no_validation_unless_configured(tmp_path):
    validations = _RecordingLauncher([])
    trigger = BuilderTrigger(
        _settings(tmp_path), runner=_ordered_runner([]), validation_launcher=validations
    )
    await trigger._run_builder_once()
    await trigger._reap_worker(_Proc(0))
    assert validations.calls == []


def test_multi_campaign_validation_covers_the_root(tmp_path):
    trigger = BuilderTrigger(
        _settings(
            tmp_path,
            output_nexus=None,
            output_root=tmp_path / "out",
            validation_python="py",
        )
    )
    assert trigger.validation_command()[2:] == ["--output-root", str(tmp_path / "out")]


def test_the_busy_exit_matches_the_workers():
    from damnit_api.consumer import builder_trigger
    from damnit_api.metadata import hzdr_containers

    assert builder_trigger.BUSY_EXIT == hzdr_containers.BUSY_EXIT


@pytest.mark.asyncio
@pytest.mark.parametrize("returncode", [3, 4, 5])
async def test_no_validation_after_a_relink_or_busy_worker(tmp_path, returncode):
    validations = _RecordingLauncher([])
    trigger = BuilderTrigger(
        _settings(tmp_path, validation_python="py"),
        validation_launcher=validations,
    )
    await trigger._reap_worker(_Proc(returncode))
    assert validations.calls == []


class _HeldProc:
    """A validation that runs until released."""

    def __init__(self) -> None:
        self.done = asyncio.Event()

    async def wait(self) -> int:
        await self.done.wait()
        return 0


@pytest.mark.asyncio
async def test_validations_never_overlap_and_coalesce_to_one_followup(
    tmp_path, monkeypatch
):
    trigger = BuilderTrigger(_settings(tmp_path, validation_python="py"))
    started: list[_HeldProc] = []

    async def spawn(cmd, log_path):
        await asyncio.sleep(0)
        proc = _HeldProc()
        started.append(proc)
        return proc

    monkeypatch.setattr(trigger, "_spawn", spawn)
    for _ in range(4):  # a burst: the first runs, the rest coalesce
        await trigger._start_validation("test")
    assert len(started) == 1
    started[0].done.set()
    await asyncio.sleep(0)
    for _ in range(5):
        await asyncio.sleep(0)
    assert len(started) == 2  # exactly one follow-up
    started[1].done.set()
    await trigger.wait_for_workers()
    assert len(started) == 2
    assert trigger._validating is False


def test_the_gate_s_counts_reach_the_api_log(tmp_path, caplog):
    (tmp_path / ".validation.json").write_text(
        json.dumps({
            "master": "campaign.nxs",
            "summary": {
                "passed": True,
                "master_errors": 0,
                "containers": 7,
                "container_errors": 0,
                "subentries": 2,
                "subentries_certified": 0,
            },
        })
    )
    trigger = BuilderTrigger(_settings(tmp_path, validation_python="py"))
    with caplog.at_level("INFO"):
        trigger.validation_finished(0)
    assert "campaign.nxs: passed; master 0 error(s); 7 container(s)" in caplog.text
    with caplog.at_level("INFO"):
        trigger.validation_finished(2)
    assert "could not run" in caplog.text


def test_a_report_older_than_the_run_is_not_logged_as_its_result(tmp_path, caplog):
    """Under OUTPUT_ROOT a campaign the gate skipped keeps an old report.

    Judged by the report's validated_at (this host's clock), not by the
    file's mtime, which a share's server sets from its own clock.
    """
    started = time.time()

    def report(name: str, passed: bool, at: float | None) -> Path:
        folder = tmp_path / name
        folder.mkdir()
        path = folder / ".validation.json"
        body = {"master": f"{name}.nxs", "summary": {"passed": passed}}
        if at is not None:
            body["validated_at"] = datetime.fromtimestamp(at, UTC).isoformat()
        path.write_text(json.dumps(body))
        return path

    report("old", False, started - 7 * 24 * 3600)
    report("unstamped", False, None)
    fresh = report("new", True, started + 1)
    # A share whose clock runs behind: the fresh file looks a week old.
    week_ago = started - 7 * 24 * 3600
    os.utime(fresh, (week_ago, week_ago))
    trigger = BuilderTrigger(
        _settings(
            tmp_path, output_nexus=None, output_root=tmp_path, validation_python="py"
        )
    )
    trigger._validation_started = started
    with caplog.at_level("INFO"):
        trigger.validation_finished(0)
    assert "new.nxs: passed" in caplog.text
    assert "old.nxs" not in caplog.text
    assert "unstamped.nxs" not in caplog.text


def test_a_busy_worker_is_not_logged_as_an_error(tmp_path, caplog):
    trigger = BuilderTrigger(_settings(tmp_path))
    with caplog.at_level("INFO"):
        trigger.worker_finished(5)
    assert "found the campaign busy" in caplog.text
    assert not [r for r in caplog.records if r.levelname == "ERROR"]
