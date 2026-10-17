"""The Redis queue of the worker (taskiq broker) against a real Redis."""

import asyncio
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis
from taskiq.message import BrokerMessage

import app.infrastructure.tasks.broker as broker_module
from app.infrastructure.redis.client import report_redis_failure
from app.infrastructure.tasks.broker import TimeoutListQueueBroker
from tests.conftest import LogEvents
from tests.integration.conftest import UNREACHABLE_REDIS_URL

QUEUE = "taskiq:test-notifications"


@pytest.fixture
async def broker(redis: Redis, redis_url: str) -> AsyncIterator[TimeoutListQueueBroker]:
    # redis: Redis is flushed before and after the test.
    queue = TimeoutListQueueBroker(redis_url, queue_name=QUEUE)
    yield queue
    await queue.connection_pool.disconnect()


def message(text: str) -> BrokerMessage:
    return BrokerMessage(task_id=text, task_name="t", message=text.encode(), labels={})


async def test_a_queued_message_comes_out(broker: TimeoutListQueueBroker) -> None:
    await broker.kick(message("wake up"))

    async with asyncio.timeout(5):
        received = await anext(broker.listen())

    assert received == b"wake up"


async def test_an_idle_queue_outlives_the_socket_timeout(
    redis_url: str, redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stock listener died here with TimeoutError after redis-py's socket timeout."""
    monkeypatch.setattr(broker_module, "_BRPOP_TIMEOUT_SECONDS", 1)
    broker = TimeoutListQueueBroker(redis_url, queue_name=QUEUE, socket_timeout=1.5)
    listener = broker.listen()
    pending = asyncio.ensure_future(anext(listener))

    await asyncio.sleep(3.5)  # several empty BRPOP rounds, each longer than half the timeout
    assert not pending.done(), pending.exception() if pending.done() else None
    await redis.lpush(QUEUE, b"late")
    async with asyncio.timeout(5):
        assert await pending == b"late"
    await listener.aclose()
    await broker.connection_pool.disconnect()


async def test_redis_failures_are_reported_and_retried(
    monkeypatch: pytest.MonkeyPatch, log_events: LogEvents
) -> None:
    monkeypatch.setattr(broker_module, "_RECONNECT_DELAY_SECONDS", 0.01)
    reported = 0
    retried = asyncio.Event()

    def report(op: str, error: BaseException) -> None:
        nonlocal reported
        report_redis_failure(op, error)
        reported += 1
        if reported == 2:
            retried.set()

    monkeypatch.setattr(broker_module, "report_redis_failure", report)
    broker = TimeoutListQueueBroker(UNREACHABLE_REDIS_URL, queue_name=QUEUE)
    listener = broker.listen()
    pending = asyncio.ensure_future(anext(listener))

    async with asyncio.timeout(5):
        await retried.wait()

    assert not pending.done(), "a Redis outage must not end the listener"
    assert {e["op"] for e in log_events() if e["event"] == "redis_unavailable"} == {
        "task_queue_pop"
    }
    pending.cancel()
    await broker.connection_pool.disconnect()
