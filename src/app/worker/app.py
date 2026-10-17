"""The notification worker on taskiq.

`taskiq scheduler` puts a `deliver_due_notifications` message into the Redis
queue every WORKER__INTERVAL_SECONDS; `taskiq worker` runs it: it drains the
due rows of the notifications table, one transaction per notification. The
table stays the source of truth (docs/db-design.md, 10.3): a lost message, a
restarted worker or a second scheduler cost a delay or an empty run, never a
lost or doubled notification (SKIP LOCKED).
"""

import asyncio
import logging
from collections.abc import Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dishka import AsyncContainer
from taskiq import AsyncBroker, AsyncTaskiqDecoratedTask, TaskiqEvents, TaskiqScheduler, TaskiqState
from taskiq.schedule_sources import LabelScheduleSource

from app.application.notifications.deliver_next_notification import DeliverNextNotification
from app.config import Settings
from app.infrastructure.tasks.broker import create_redis_broker
from app.ioc.container import create_container

logger = logging.getLogger("app.worker")

DELIVER_TASK_NAME = "deliver_due_notifications"


async def drain(container: AsyncContainer, *, limit: int, stop: asyncio.Event | None = None) -> int:
    """Process due notifications until none is left, `limit` is reached or `stop` is set.

    Each one gets its own request scope: its own session, transaction and lock.
    A stop request is honoured between notifications, never in the middle of one.
    """
    processed = 0
    while processed < limit and not (stop is not None and stop.is_set()):
        async with container() as request:
            deliver = await request.get(DeliverNextNotification)
            if await deliver() is None:
                break
        processed += 1
    return processed


@dataclass(slots=True)
class WorkerApp:
    broker: AsyncBroker
    scheduler: TaskiqScheduler
    deliver: AsyncTaskiqDecoratedTask[[], Coroutine[Any, Any, int]]
    """The periodic task; `await deliver.kiq()` queues a run by hand."""
    _container: AsyncContainer | None = field(default=None, repr=False)


def create_worker_app(settings: Settings, *, broker: AsyncBroker | None = None) -> WorkerApp:
    """The broker, the scheduler and the task. `broker` replaces the Redis one (tests).

    The dependency container lives in the worker process only: it is created on
    WORKER_STARTUP and closed on WORKER_SHUTDOWN, so the scheduler process never
    connects to the database.
    """
    config = settings.worker
    broker = broker or create_redis_broker(
        settings.redis.url.get_secret_value(), queue_name=config.queue_name
    )

    @broker.task(
        task_name=DELIVER_TASK_NAME,
        # A fixed id: a restarted scheduler keeps one schedule, not a new one per start.
        schedule=[{"interval": config.interval_seconds, "schedule_id": DELIVER_TASK_NAME}],
    )
    async def deliver_due_notifications() -> int:
        """Send what is due; returns how many notifications were processed."""
        if app._container is None:  # pragma: no cover  # taskiq runs startup first
            raise RuntimeError("The worker container is not started")
        try:
            processed = await drain(app._container, limit=config.batch_size)
        except Exception:
            # The database may be restarting: the next tick tries again. The stale
            # heartbeat makes the container unhealthy meanwhile.
            logger.exception("worker_iteration_failed")
            return 0
        _touch(config.heartbeat_file)
        if processed:
            logger.info("notifications_drained", extra={"count": processed})
        return processed

    app = WorkerApp(
        broker=broker,
        scheduler=TaskiqScheduler(broker, sources=[LabelScheduleSource(broker)]),
        deliver=deliver_due_notifications,
    )

    @broker.on_event(TaskiqEvents.WORKER_STARTUP)
    async def start(_: TaskiqState) -> None:
        app._container = create_container(settings)
        _touch(config.heartbeat_file)  # healthy until the first tick proves otherwise
        logger.info(
            "worker_started",
            extra={"version": settings.app_version, "interval": config.interval_seconds},
        )

    @broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
    async def stop(_: TaskiqState) -> None:
        if app._container is not None:
            await app._container.close()
            app._container = None
        logger.info("worker_stopped")

    return app


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
