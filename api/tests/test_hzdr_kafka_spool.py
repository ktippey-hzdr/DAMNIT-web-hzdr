"""Tests for the durable Kafka trigger spool consumer.

No Docker or live broker: a tiny in-memory ``_FakeKafkaConsumer`` stands in for
``kafka.KafkaConsumer`` and lets us assert the same durability properties the
ASAPO consumer guarantees (see hzdr/docs/status/integration-roadmap.md §Durable Spool):

    1. claim does not commit the offset until ack (write-before-commit)
    2. the committed offset is exactly last-consumed + 1
    3. dedup by event_id (redelivery after an uncommitted crash is idempotent)
    4. an empty poll commits nothing (no spurious offset advance)
    5. non-object records are skipped, not spooled
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, NamedTuple

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from damnit_api.consumer.kafka import KafkaSpoolConsumer
from damnit_api.consumer.spool import SpoolConfig

# ---------------------------------------------------------------------------
# In-memory fake broker
# ---------------------------------------------------------------------------


class _OffsetAndMetadata(NamedTuple):
    offset: int
    metadata: str | None


class _TopicPartition(NamedTuple):
    topic: str
    partition: int


class _FakeRecord(NamedTuple):
    value: object
    offset: int


class _FakeKafkaConsumer:
    """Single-topic, single-partition in-memory Kafka consumer stand-in."""

    def __init__(self, topic: str = "triggers", partition: int = 0) -> None:
        self.tp = _TopicPartition(topic, partition)
        self._log: list[object] = []
        self._delivered = 0
        self.committed: int | None = None
        self.commit_calls: list[dict] = []
        self.closed = False

    def add(self, value: object) -> None:
        self._log.append(value)

    def poll(self, timeout_ms: int, max_records: int | None = None) -> dict:
        start = self._delivered
        end = len(self._log)
        if max_records is not None:
            end = min(end, start + max_records)
        if start >= end:
            return {}
        recs = [_FakeRecord(self._log[i], i) for i in range(start, end)]
        self._delivered = end
        return {self.tp: recs}

    def commit(self, offsets: dict) -> None:
        self.commit_calls.append(offsets)
        self.committed = offsets[self.tp].offset

    def close(self) -> None:
        self.closed = True

    def restart_uncommitted(self) -> None:
        """Rewind delivery to the last committed offset (crash + restart)."""
        self._delivered = self.committed or 0


def _make_event(event_id: str, shot_number: int = 1) -> dict:
    return {
        "schema_version": "hzdr-event-v1",
        "event_id": event_id,
        "experiment_id": "campaign-a",
        "shot_id": f"shot-{shot_number:06d}",
        "shot_number": shot_number,
        "source": "DRACO-Trigger",
        "kind": "trigger",
        "timestamp": "2025-01-15T10:00:00Z",
        "transport": "kafka",
        "trigger_role": "shot",
        "payload_ref": {"topic": "triggers", "partition": 0, "offset": shot_number},
    }


def _make_consumer(
    fake: _FakeKafkaConsumer, spool_dir: Path, campaign: str = "campaign-a"
) -> KafkaSpoolConsumer:
    cfg = SpoolConfig(
        campaign=campaign,
        consumer_group="test-kafka-group",
        spool_dir=spool_dir,
        poll_interval=0.05,
        batch_size=10,
        filename="trigger.jsonl",
    )
    return KafkaSpoolConsumer(config=cfg, consumer=fake, poll_timeout_ms=10)


async def _run_briefly(consumer: KafkaSpoolConsumer, seconds: float = 0.2) -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(consumer.run(stop))
    await asyncio.sleep(seconds)
    stop.set()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await consumer.aclose()


def _spool_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_writes_then_commits_offset(tmp_path: Path) -> None:
    fake = _FakeKafkaConsumer()
    for i in range(1, 4):
        fake.add(_make_event(f"evt-{i}", i))
    consumer = _make_consumer(fake, tmp_path)

    await _run_briefly(consumer)

    assert len(_spool_lines(consumer.config.events_jsonl)) == 3
    assert fake.committed == 3  # last offset (2) + 1
    assert fake.closed is True


@pytest.mark.asyncio
async def test_commit_token_is_last_offset_plus_one(tmp_path: Path) -> None:
    fake = _FakeKafkaConsumer()
    fake.add(_make_event("evt-1", 1))
    fake.add(_make_event("evt-2", 2))
    consumer = _make_consumer(fake, tmp_path)

    messages, token = await consumer._claim()

    assert [m["event_id"] for m in messages] == ["evt-1", "evt-2"]
    assert token == {fake.tp: _OffsetAndMetadata(2, None)}


@pytest.mark.asyncio
async def test_empty_poll_commits_nothing(tmp_path: Path) -> None:
    fake = _FakeKafkaConsumer()
    consumer = _make_consumer(fake, tmp_path)

    await _run_briefly(consumer)

    assert fake.commit_calls == []
    assert _spool_lines(consumer.config.events_jsonl) == []


@pytest.mark.asyncio
async def test_dedup_on_uncommitted_replay(tmp_path: Path) -> None:
    fake = _FakeKafkaConsumer()
    fake.add(_make_event("evt-1", 1))
    fake.add(_make_event("evt-2", 2))

    consumer1 = _make_consumer(fake, tmp_path)
    await _run_briefly(consumer1)
    assert len(_spool_lines(consumer1.config.events_jsonl)) == 2

    # Simulate a crash that lost the committed position; the same events are
    # redelivered to a fresh consumer sharing the spool file.
    fake.restart_uncommitted()
    fake._delivered = 0
    consumer2 = _make_consumer(fake, tmp_path)
    await _run_briefly(consumer2)

    # Dedup-by-event_id keeps the spool at two unique lines.
    assert len(_spool_lines(consumer2.config.events_jsonl)) == 2


@pytest.mark.asyncio
async def test_non_object_record_is_skipped(tmp_path: Path) -> None:
    fake = _FakeKafkaConsumer()
    fake.add(_make_event("evt-1", 1))
    fake.add("not-a-dict")
    fake.add(_make_event("evt-2", 2))
    consumer = _make_consumer(fake, tmp_path)

    await _run_briefly(consumer)

    lines = _spool_lines(consumer.config.events_jsonl)
    assert len(lines) == 2
    # The malformed record still advances the offset so it is not redelivered.
    assert fake.committed == 3


@pytest.mark.asyncio
async def test_ack_with_empty_token_is_noop(tmp_path: Path) -> None:
    fake = _FakeKafkaConsumer()
    consumer = _make_consumer(fake, tmp_path)

    await consumer._ack({})

    assert fake.commit_calls == []


# ---------------------------------------------------------------------------
# Review findings R01, R02, R08 (2026-11-12 review; emulator E9, E10, E17)
# ---------------------------------------------------------------------------


def _refuse_writes(consumer: KafkaSpoolConsumer, event_ids: set[str]) -> list[str]:
    """Refuse the first write of each named event with ENOSPC, then allow it."""
    import errno

    original = consumer.consume_one
    refused: list[str] = []

    def consume_one(message):
        event_id = message.get("event_id")
        if event_id in event_ids and event_id not in refused:
            refused.append(event_id)
            raise OSError(errno.ENOSPC, "No space left on device")
        return original(message)

    consumer.consume_one = consume_one  # type: ignore[method-assign]
    return refused


@pytest.mark.asyncio
async def test_r01_a_refused_write_is_retried_not_committed_past(
    tmp_path: Path,
) -> None:
    fake = _FakeKafkaConsumer()
    fake.add(_make_event("evt-0", 1))
    fake.add(_make_event("evt-1", 2))
    consumer = _make_consumer(fake, tmp_path)
    consumer.config.batch_size = 1  # offsets 0 and 1 in successive batches
    refused = _refuse_writes(consumer, {"evt-0"})

    await _run_briefly(consumer, seconds=0.4)

    assert refused == ["evt-0"]
    ids = [
        json.loads(line)["event_id"]
        for line in _spool_lines(consumer.config.events_jsonl)
    ]
    assert ids == ["evt-0", "evt-1"]
    # No commit ever named a position past an event that was not on disk.
    assert [call[fake.tp].offset for call in fake.commit_calls] == [1, 2]


@pytest.mark.asyncio
async def test_r01_a_refusal_partway_through_a_batch_keeps_the_whole_batch(
    tmp_path: Path,
) -> None:
    fake = _FakeKafkaConsumer()
    for i in range(3):
        fake.add(_make_event(f"evt-{i}", i + 1))
    consumer = _make_consumer(fake, tmp_path)
    refused = _refuse_writes(consumer, {"evt-1"})

    await _run_briefly(consumer, seconds=0.4)

    assert refused == ["evt-1"]
    ids = [
        json.loads(line)["event_id"]
        for line in _spool_lines(consumer.config.events_jsonl)
    ]
    assert ids == ["evt-0", "evt-1", "evt-2"]  # evt-0 written once, not twice
    assert [call[fake.tp].offset for call in fake.commit_calls] == [3]


@pytest.mark.asyncio
async def test_r02_a_torn_tail_is_quarantined_before_the_next_append(
    tmp_path: Path,
) -> None:
    from damnit_api.metadata.hzdr_nexus import load_normalized_events

    fake = _FakeKafkaConsumer()
    fake.add(_make_event("evt-1", 1))
    consumer = _make_consumer(fake, tmp_path)
    spool = consumer.config.events_jsonl
    spool.parent.mkdir(parents=True, exist_ok=True)
    good = json.dumps(_make_event("evt-0", 9), sort_keys=True)
    spool.write_text(good + '\n{"event_id": "interrupted', encoding="utf-8")

    await _run_briefly(consumer)

    lines = _spool_lines(spool)
    assert [json.loads(line)["event_id"] for line in lines] == ["evt-0", "evt-1"]
    quarantined = spool.with_name(spool.name + ".torn-1")
    assert quarantined.read_text(encoding="utf-8") == '{"event_id": "interrupted'
    assert fake.committed == 1
    # The builder reads the repaired spool.
    assert [e["event_id"] for e in load_normalized_events([spool])] == [
        "evt-0",
        "evt-1",
    ]


def test_r02_a_complete_record_missing_only_its_newline_is_kept(tmp_path: Path) -> None:
    from damnit_api.consumer.spool import _append_event_durable

    spool = tmp_path / "events.jsonl"
    spool.write_text(json.dumps({"event_id": "a"}), encoding="utf-8")
    _append_event_durable(spool, {"event_id": "b"})
    assert [json.loads(x)["event_id"] for x in spool.read_text().splitlines()] == [
        "a",
        "b",
    ]
    assert not list(tmp_path.glob("*.torn-*"))


def test_r02_an_interior_malformed_line_is_left_alone(tmp_path: Path) -> None:
    from damnit_api.consumer.spool import _append_event_durable

    spool = tmp_path / "events.jsonl"
    spool.write_text(
        '{"event_id": "a"}\nnot json\n{"event_id": "c"}\n', encoding="utf-8"
    )
    _append_event_durable(spool, {"event_id": "d"})
    assert spool.read_text().splitlines()[1] == "not json"
    assert not list(tmp_path.glob("*.torn-*"))


@pytest.mark.asyncio
async def test_r08_the_spool_keeps_the_broker_coordinates(tmp_path: Path) -> None:
    fake = _FakeKafkaConsumer(topic="planet.watchdog.events", partition=1)
    authority_shaped = _make_event("evt-a", 1)
    authority_shaped["payload_ref"] = {"channel_id": "Draco01", "run_id": "r1"}
    fake.add(authority_shaped)
    stale = _make_event("evt-b", 2)
    stale["payload_ref"] = {
        "topic": "old-topic",
        "partition": 0,
        "offset": 99,
        "path": "/x",
    }
    fake.add(stale)
    consumer = _make_consumer(fake, tmp_path)

    await _run_briefly(consumer)

    refs = {
        json.loads(line)["event_id"]: json.loads(line)["payload_ref"]
        for line in _spool_lines(consumer.config.events_jsonl)
    }
    assert refs["evt-a"] == {
        "channel_id": "Draco01",
        "run_id": "r1",
        "topic": "planet.watchdog.events",
        "partition": 1,
        "offset": 0,
    }
    assert refs["evt-b"]["topic"] == "planet.watchdog.events"
    assert (refs["evt-b"]["partition"], refs["evt-b"]["offset"]) == (1, 1)
    assert refs["evt-b"]["path"] == "/x"
    assert refs["evt-b"]["producer_position"] == {
        "topic": "old-topic",
        "partition": 0,
        "offset": 99,
    }
    # The producer's own dict is not mutated.
    assert stale["payload_ref"]["offset"] == 99
