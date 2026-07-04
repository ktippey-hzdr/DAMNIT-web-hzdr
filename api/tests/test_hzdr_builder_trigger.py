"""Tests for the in-process, debounced builder auto-trigger.

Everything here is local: the builder invocation is faked with an in-process
runner (no broker, no Mongo, no ASAPO), except for two tests that exercise the
default subprocess runner with a trivial ``python -c`` command.

Covered properties (from the auto-trigger task):

    1. a burst of on_new_events/schedule calls coalesces into one run
    2. events arriving during a run schedule exactly one follow-up run
    3. auto-trigger is off by default (from_settings → None, no run)
    4. a builder failure is logged and does not kill the worker loop
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from damnit_api.consumer.builder_trigger import BuilderAutoTrigger
from damnit_api.consumer.spool import HZDRSpoolConsumer, SpoolConfig


class _ConcreteConsumer(HZDRSpoolConsumer):
    """Minimal consumer whose claim/ack are inert; only the trigger matters."""

    async def _claim(self) -> tuple[list, dict]:
        return [], {}

    async def _ack(self, token: object) -> None:
        pass


# ---------------------------------------------------------------------------
# Debounce / coalescing behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debounce_coalesces_burst_into_one_run():
    calls: list[list[str]] = []

    async def runner(cmd):  # noqa: RUF029 -- runner contract is a coroutine
        calls.append(list(cmd))

    trig = BuilderAutoTrigger(["build"], debounce_seconds=0.05, runner=runner)
    trig.start()
    trig.schedule()
    trig.schedule()
    trig.schedule()
    await asyncio.sleep(0.2)
    await trig.aclose()

    assert calls == [["build"]]


@pytest.mark.asyncio
async def test_events_during_run_schedule_exactly_one_followup():
    calls: list[list[str]] = []
    first_started = asyncio.Event()
    release = asyncio.Event()

    async def runner(cmd):
        idx = len(calls)
        calls.append(list(cmd))
        if idx == 0:
            first_started.set()
            await release.wait()

    trig = BuilderAutoTrigger(["build"], debounce_seconds=0.01, runner=runner)
    trig.start()
    trig.schedule()
    await asyncio.wait_for(first_started.wait(), 1.0)

    # Multiple schedules while the first run is blocked must collapse to one.
    trig.schedule()
    trig.schedule()
    trig.schedule()
    release.set()

    await asyncio.sleep(0.1)
    await trig.aclose()

    assert len(calls) == 2, f"expected one follow-up run, got {len(calls)}"


@pytest.mark.asyncio
async def test_no_run_without_schedule():
    calls: list[list[str]] = []

    async def runner(cmd):  # noqa: RUF029 -- runner contract is a coroutine
        calls.append(list(cmd))

    trig = BuilderAutoTrigger(["build"], debounce_seconds=0.01, runner=runner)
    trig.start()
    await asyncio.sleep(0.1)
    await trig.aclose()

    assert calls == []


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_builder_failure_logged_and_worker_survives(caplog):
    calls: list[list[str]] = []

    async def runner(cmd):  # noqa: RUF029 -- runner contract is a coroutine
        calls.append(list(cmd))
        if len(calls) == 1:
            msg = "boom"
            raise RuntimeError(msg)

    trig = BuilderAutoTrigger(["build"], debounce_seconds=0.01, runner=runner)
    trig.start()

    with caplog.at_level("ERROR"):
        trig.schedule()
        await asyncio.sleep(0.1)

    assert any("run failed" in record.message for record in caplog.records)

    # The worker must still be alive: a later event triggers another run.
    trig.schedule()
    await asyncio.sleep(0.1)
    await trig.aclose()

    assert len(calls) == 2


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aclose_cancels_inflight_run_cleanly():
    started = asyncio.Event()
    release = asyncio.Event()

    async def runner(cmd):
        started.set()
        await release.wait()  # never released; aclose must cancel this

    trig = BuilderAutoTrigger(["build"], debounce_seconds=0.0, runner=runner)
    trig.start()
    trig.schedule()
    await asyncio.wait_for(started.wait(), 1.0)

    # Must return promptly instead of hanging on the un-released runner.
    await asyncio.wait_for(trig.aclose(), 1.0)

    # schedule() after close is a no-op and does not raise.
    trig.schedule()


# ---------------------------------------------------------------------------
# Consumer wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consumer_on_new_events_schedules_builder(tmp_path):
    calls: list[list[str]] = []

    async def runner(cmd):  # noqa: RUF029 -- runner contract is a coroutine
        calls.append(list(cmd))

    trig = BuilderAutoTrigger(["build"], debounce_seconds=0.02, runner=runner)
    cfg = SpoolConfig(campaign="c", consumer_group="g", spool_dir=tmp_path)
    consumer = _ConcreteConsumer(cfg, trig)
    trig.start()

    consumer.on_new_events([tmp_path / "events.jsonl"])
    await asyncio.sleep(0.1)
    await consumer.aclose()

    assert calls == [["build"]]


@pytest.mark.asyncio
async def test_consumer_without_trigger_is_a_noop(tmp_path):
    cfg = SpoolConfig(campaign="c", consumer_group="g", spool_dir=tmp_path)
    consumer = _ConcreteConsumer(cfg)  # no trigger — auto-trigger disabled

    # Must not raise and there is nothing to schedule.
    consumer.on_new_events([tmp_path / "events.jsonl"])
    await consumer.aclose()


# ---------------------------------------------------------------------------
# Settings / opt-in
# ---------------------------------------------------------------------------


def test_from_settings_returns_none_when_disabled():
    from damnit_api.shared.settings import HZDRKafkaSpoolSettings, HZDRSpoolSettings

    assert BuilderAutoTrigger.from_settings(HZDRSpoolSettings(), label="x") is None
    assert (
        BuilderAutoTrigger.from_settings(HZDRKafkaSpoolSettings(), label="x") is None
    )


def test_from_settings_builds_trigger_when_enabled():
    from damnit_api.shared.settings import HZDRSpoolSettings

    cfg = HZDRSpoolSettings(
        builder_auto_trigger=True,
        builder_command=["python", "build.py"],
        builder_debounce_seconds=1.5,
    )
    trig = BuilderAutoTrigger.from_settings(cfg, label="asapo-spool")
    assert isinstance(trig, BuilderAutoTrigger)
    assert trig._command == ["python", "build.py"]
    assert trig._debounce == pytest.approx(1.5)


def test_settings_reject_auto_trigger_without_command():
    from pydantic import ValidationError

    from damnit_api.shared.settings import HZDRKafkaSpoolSettings, HZDRSpoolSettings

    with pytest.raises(ValidationError, match="BUILDER_COMMAND"):
        HZDRSpoolSettings(builder_auto_trigger=True)
    with pytest.raises(ValidationError, match="BUILDER_COMMAND"):
        HZDRKafkaSpoolSettings(builder_auto_trigger=True)


# ---------------------------------------------------------------------------
# Default subprocess runner (local, trivial commands)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subprocess_runner_success():
    trig = BuilderAutoTrigger(
        [sys.executable, "-c", "import sys; sys.exit(0)"],
        debounce_seconds=0.0,
    )
    # Should complete without raising.
    await trig._run_subprocess(trig._command)


@pytest.mark.asyncio
async def test_subprocess_runner_failure_is_logged(caplog):
    trig = BuilderAutoTrigger(
        [sys.executable, "-c", "print('nope'); import sys; sys.exit(3)"],
        debounce_seconds=0.0,
    )
    with caplog.at_level("ERROR"):
        await trig._run_subprocess(trig._command)

    assert any("exited with code 3" in record.message for record in caplog.records)
