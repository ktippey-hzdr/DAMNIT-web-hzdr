"""Lifespan wiring for the HZDR durable spool consumers + builder auto-trigger.

Extracted verbatim from ``main.py`` so the fork's diff against upstream
``main.py`` shrinks to a single ``async with`` hook.  This is entirely fork-only
machinery — upstream has no spool consumers — so keeping the startup/shutdown
here means an upstream PR that touches ``main.py`` never has to carry (or review)
the spool/trigger wiring.

``spool_lifespan`` starts whichever consumers are enabled (ASAPO, Kafka), wires
the optional debounced :class:`~.builder_trigger.BuilderTrigger` to each, yields
while they run in background tasks, then stops and closes them on exit.  When
nothing is enabled it is a no-op context manager.  The heavy transport imports
(ASAPO SDK, confluent-kafka) stay lazy inside the conditionals so an unused
transport never has to be installed.
"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from ..shared.settings import Settings


@asynccontextmanager
async def spool_lifespan(settings: Settings, logger: Any) -> AsyncIterator[None]:
    """Run the enabled spool consumers + builder trigger for the app's lifetime.

    Mirrors the previous inline ``main.py`` block one-for-one: same enable gates,
    same structured log calls, same shutdown order (stop event → cancel tasks →
    await → ``aclose``).  The cleanup runs in a ``finally`` so an exception while
    the app is serving cannot leak the background tasks or consumer resources.
    """
    spool_root = settings.damnit_path or Path.cwd()
    spool_stop = asyncio.Event()
    spool_consumers = []
    spool_tasks = []
    # ASAPO spool writes events.jsonl (--events-jsonl); Kafka spool writes
    # trigger.jsonl (--trigger-jsonl).  Collected here so the optional builder
    # auto-trigger reruns the builder against exactly the running spool files.
    builder_events_jsonl = []
    builder_trigger_jsonl = []
    # ...plus each consumer's shared ``_unassigned`` spool (decision D1), which
    # every campaign's build reads so it can route those events itself.
    unassigned_events_jsonl = []
    unassigned_trigger_jsonl = []
    # ...and, for the multi-campaign builder, each spool's root and file name.
    events_spools = []
    trigger_spools = []
    if settings.hzdr_spool.enabled:
        from .asapo import AsapoSpoolConsumer

        asapo_consumer = AsapoSpoolConsumer.from_settings(spool_root)
        spool_consumers.append(asapo_consumer)
        builder_events_jsonl.append(asapo_consumer.config.events_jsonl)
        unassigned_events_jsonl.append(asapo_consumer.config.unassigned_jsonl)
        events_spools.append((
            asapo_consumer.config.spool_dir,
            asapo_consumer.config.filename,
        ))
        spool_tasks.append(asyncio.create_task(asapo_consumer.run(spool_stop)))
        logger.info(
            "ASAPO spool consumer started",
            campaign=settings.hzdr_spool.campaign,
            broker_kind=settings.hzdr_spool.broker_kind,
            broker=(
                settings.hzdr_spool.broker_url
                if settings.hzdr_spool.broker_kind == "http"
                else settings.hzdr_spool.asapo_endpoint
            ),
        )
    if settings.hzdr_kafka_spool.enabled:
        from .kafka import KafkaSpoolConsumer

        kafka_consumer = KafkaSpoolConsumer.from_settings(spool_root)
        spool_consumers.append(kafka_consumer)
        builder_trigger_jsonl.append(kafka_consumer.config.events_jsonl)
        unassigned_trigger_jsonl.append(kafka_consumer.config.unassigned_jsonl)
        trigger_spools.append((
            kafka_consumer.config.spool_dir,
            kafka_consumer.config.filename,
        ))
        spool_tasks.append(asyncio.create_task(kafka_consumer.run(spool_stop)))
        logger.info(
            "Kafka spool consumer started",
            campaign=settings.hzdr_kafka_spool.campaign,
            bootstrap_servers=settings.hzdr_kafka_spool.bootstrap_servers,
            topics=settings.hzdr_kafka_spool.topics,
        )

    if settings.hzdr_builder.enabled and spool_consumers:
        from .builder_trigger import BuilderTrigger

        builder_trigger = BuilderTrigger(
            settings.hzdr_builder,
            events_jsonl=builder_events_jsonl,
            trigger_jsonl=builder_trigger_jsonl,
            unassigned_events_jsonl=unassigned_events_jsonl,
            unassigned_trigger_jsonl=unassigned_trigger_jsonl,
            events_spools=events_spools,
            trigger_spools=trigger_spools,
        )
        for consumer in spool_consumers:
            consumer.on_new_events_hook = builder_trigger.notify
        spool_tasks.append(asyncio.create_task(builder_trigger.run(spool_stop)))
        _log_builder_started(settings, logger)
    elif settings.hzdr_builder.enabled:
        logger.warning(
            "DW_API_HZDR_BUILDER__ENABLED=true but no spool consumer is "
            "enabled; nothing will trigger the builder"
        )

    try:
        yield
    finally:
        if spool_tasks:
            spool_stop.set()
            for task in spool_tasks:
                task.cancel()
            for task in spool_tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        for consumer in spool_consumers:
            await consumer.aclose()


def _log_builder_started(settings: Settings, logger: Any) -> None:
    builder = settings.hzdr_builder
    if not builder.multi_campaign:
        logger.info(
            "Builder auto-trigger started",
            output_nexus=str(builder.output_nexus),
            debounce_seconds=builder.debounce_seconds,
        )
        return
    logger.info(
        "Builder auto-trigger started",
        output_root=str(builder.output_root),
        sources_file=str(builder.catalog_file),
        campaigns=builder.campaigns,
        debounce_seconds=builder.debounce_seconds,
    )
    _warn_if_catalog_not_served(settings, logger)


def _warn_if_catalog_not_served(settings: Settings, logger: Any) -> None:
    """The shared catalog only reaches the UI if the API reads that same file.

    It is also where Review matches records rulings, so a mismatch means the
    builds never see them.
    """
    built = settings.hzdr_builder.catalog_file
    served = settings.metadata.sources_file
    if built is None:
        return
    if (
        settings.metadata.provider == "local"
        and served is not None
        and served.resolve() == built.resolve()
    ):
        return
    logger.warning(
        "The multi-campaign builder writes a catalog the API does not serve; "
        "set DW_API_METADATA__PROVIDER=local and "
        "DW_API_METADATA__SOURCES_FILE to it",
        built=str(built),
        served=str(served),
    )
