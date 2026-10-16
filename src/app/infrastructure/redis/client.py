"""Redis is auxiliary (cache, idempotency, rate limit), so every call fails open:
an error is logged and counted, and the request continues without the feature."""

import structlog
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

from app.application.interfaces.health import AbstractHealthProbe
from app.infrastructure.metrics.prometheus import REDIS_ERRORS

logger = structlog.get_logger(__name__)

# Catch these around every Redis call. TimeoutError and ConnectionError are the
# builtins (asyncio timeouts, sockets); redis-py raises its own subclasses of RedisError.
REDIS_FAILURES = (RedisError, TimeoutError, ConnectionError, OSError)


def create_redis(url: str, *, timeout_seconds: float) -> Redis:
    return Redis.from_url(
        url,
        socket_timeout=timeout_seconds,
        socket_connect_timeout=timeout_seconds,
        # One immediate retry heals a pooled connection broken by a Redis restart;
        # more would multiply the timeout when Redis hangs.
        retry=Retry(NoBackoff(), retries=1),
        decode_responses=True,
    )


def report_redis_failure(op: str, error: BaseException) -> None:
    REDIS_ERRORS.labels(op).inc()
    logger.warning("redis_unavailable", op=op, error=repr(error))


class RedisHealthProbe(AbstractHealthProbe):
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def check(self) -> None:
        await self._redis.ping()
