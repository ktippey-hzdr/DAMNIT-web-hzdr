"""In-process, debounced auto-trigger for the HZDR NeXus builder.

The durable spool consumers (:mod:`.spool`, :mod:`.asapo`, :mod:`.kafka`) call
:meth:`BuilderAutoTrigger.schedule` from their async loop after each batch of
new events is written and acked.  This helper collapses a burst of such calls
into a single builder run and guarantees that events arriving *during* a run
schedule exactly one follow-up run.

Design constraints (see CLAUDE.md / docs/architecture.md):

* **Single-writer per campaign.**  The builder itself takes a PID-stamped
  ``single_writer_lock`` and publishes the NeXus file + catalog atomically.
  This trigger runs the builder as an external subprocess (the same command an
  operator or cron would run), so those invariants are preserved unchanged, and
  it never spawns concurrent builds for the same campaign — a run in progress
  coalesces later triggers into one pending follow-up.

* **Non-blocking.**  ``schedule`` only flags pending work and returns
  immediately; the actual debounce wait and subprocess run happen on a
  dedicated worker task owned by this object, so the consumer poll loop is
  never blocked.

* **Failure-isolated.**  A failed builder run is logged and never propagates
  into the consumer loop; the worker keeps running so future events still
  trigger builds.

* **Opt-in.**  Consumers only create a trigger when the corresponding
  ``builder_auto_trigger`` setting is true, so existing deployments that leave
  it at the default (off) are unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..shared.settings import HZDRKafkaSpoolSettings, HZDRSpoolSettings

logger = logging.getLogger(__name__)

# A runner takes the fully-formed builder command and runs it to completion.
BuilderRunner = Callable[[Sequence[str]], Awaitable[None]]

# Cap how much subprocess output we echo into a single log line on failure.
_MAX_LOGGED_OUTPUT = 2000


class BuilderAutoTrigger:
    """Debounce/coalesce builder runs triggered by new spool events.

    Lifecycle: :meth:`start` (from the consumer's ``run`` coroutine, so a loop
    is running) spawns the worker task; :meth:`schedule` flags pending work;
    :meth:`aclose` stops the worker cleanly on shutdown.
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        debounce_seconds: float = 5.0,
        label: str = "builder",
        runner: BuilderRunner | None = None,
    ) -> None:
        self._command: list[str] = list(command)
        self._debounce = max(0.0, float(debounce_seconds))
        self._label = label
        self._runner: BuilderRunner = runner or self._run_subprocess
        self._wakeup = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    def start(self) -> None:
        """Spawn the worker task.  Must be called with a running event loop."""
        if self._task is not None:
            return
        self._closed = False
        self._task = asyncio.create_task(self._worker())

    def schedule(self) -> None:
        """Flag that new events have arrived.  Non-blocking; safe to spam."""
        if self._closed or self._task is None:
            return
        self._wakeup.set()

    async def aclose(self) -> None:
        """Stop the worker task, waiting for any in-flight build to be cancelled."""
        self._closed = True
        task = self._task
        self._task = None
        if task is None:
            return
        self._wakeup.set()  # nudge the worker out of its wait
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def _worker(self) -> None:
        """Debounce loop: wait for work, coalesce a burst, run one build.

        After a build completes, if events arrived while it was running the
        wakeup flag is still set, so the loop immediately schedules exactly one
        follow-up run.
        """
        while not self._closed:
            await self._wakeup.wait()
            if self._closed:
                return
            # Quiet period: let a burst of schedule() calls collapse into one run.
            if self._debounce:
                await asyncio.sleep(self._debounce)
            if self._closed:
                return
            # Everything observed up to this point is covered by the run below.
            # Events arriving during the run re-set the flag → one follow-up.
            self._wakeup.clear()
            try:
                await self._runner(self._command)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Auto-trigger %s run failed; consumer keeps running",
                    self._label,
                )

    async def _run_subprocess(self, command: Sequence[str]) -> None:
        """Default runner: launch the builder command as a subprocess."""
        cmd = list(command)
        logger.info("Auto-trigger %s launching: %s", self._label, " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        output = (stdout or b"").decode("utf-8", errors="replace")
        if proc.returncode != 0:
            logger.error(
                "Auto-trigger %s builder exited with code %s: %s",
                self._label,
                proc.returncode,
                output[-_MAX_LOGGED_OUTPUT:],
            )
        else:
            logger.info(
                "Auto-trigger %s builder completed (rc=0)", self._label
            )

    @classmethod
    def from_settings(
        cls,
        cfg: HZDRSpoolSettings | HZDRKafkaSpoolSettings,
        *,
        label: str,
    ) -> BuilderAutoTrigger | None:
        """Build a trigger from a spool settings block, or ``None`` if disabled.

        Returns ``None`` when ``builder_auto_trigger`` is off so callers can pass
        the result straight through to the consumer.  The settings validator
        already rejects ``builder_auto_trigger=true`` with an empty command, so a
        returned trigger always has a runnable command.
        """
        if not cfg.builder_auto_trigger:
            return None
        return cls(
            command=cfg.builder_command,
            debounce_seconds=cfg.builder_debounce_seconds,
            label=label,
        )
