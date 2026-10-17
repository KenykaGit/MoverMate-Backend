import asyncio
from collections.abc import AsyncGenerator

from redis.asyncio import Redis
from taskiq import AsyncBroker
from taskiq_redis import ListQueueBroker

from app.infrastructure.redis.client import REDIS_FAILURES, report_redis_failure

# Shorter than the socket timeout of redis-py (5 s): an idle queue must look like an
# empty answer, not like a broken connection.
_BRPOP_TIMEOUT_SECONDS = 2
_RECONNECT_DELAY_SECONDS = 1.0


class TimeoutListQueueBroker(ListQueueBroker):
    """ListQueueBroker whose listener waits for messages in short rounds.

    The stock listener blocks in BRPOP without a timeout, so on an idle queue
    redis-py's 5 s socket timeout raises TimeoutError, which it does not catch:
    the worker process dies and is restarted every few seconds. Here BRPOP
    returns empty before that, and any Redis failure is reported and retried.
    """

    async def listen(self) -> AsyncGenerator[bytes]:
        while True:
            try:
                async with Redis(connection_pool=self.connection_pool) as connection:
                    popped = await connection.brpop(
                        [self.queue_name], timeout=_BRPOP_TIMEOUT_SECONDS
                    )
            except REDIS_FAILURES as error:
                # Notifications wait in Postgres meanwhile: only the wake-up is late.
                report_redis_failure("task_queue_pop", error)
                await asyncio.sleep(_RECONNECT_DELAY_SECONDS)
                continue
            if popped is not None:
                _, message = popped
                # The broker's pool does not decode responses: always bytes.
                yield message if isinstance(message, bytes) else message.encode()


def create_redis_broker(url: str, *, queue_name: str) -> AsyncBroker:
    """A taskiq broker on a Redis list.

    Messages are only wake-up calls: what to send lives in the notifications
    table. A lost or evicted message delays delivery until the next tick and
    loses nothing (docs/adr/0011-notification-worker.md).
    """
    return TimeoutListQueueBroker(url, queue_name=queue_name)
