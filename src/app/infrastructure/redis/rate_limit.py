import time
from collections.abc import Callable

from redis.asyncio import Redis

from app.application.interfaces.rate_limiter import AbstractRateLimiter, RateLimitDecision
from app.infrastructure.redis.client import REDIS_FAILURES, report_redis_failure


class RedisRateLimiter(AbstractRateLimiter):
    """Fixed window: INCR + EXPIRE NX in one MULTI/EXEC transaction.

    Simple and O(1) per request; allows up to 2x the limit across a window
    boundary, which is acceptable for abuse protection (docs/adr/0006).
    """

    def __init__(self, redis: Redis, clock: Callable[[], float] = time.time) -> None:
        self._redis = redis
        self._clock = clock

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision | None:
        now = int(self._clock())
        window_start = now - now % window_seconds
        counter_key = f"rl:{key}:{window_start}"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(counter_key)
                # NX: the first request of the window sets the TTL, later ones keep it.
                pipe.expire(counter_key, window_seconds, nx=True)
                count, _ = await pipe.execute()
        except REDIS_FAILURES as error:
            report_redis_failure("rate_limit", error)
            return None

        return RateLimitDecision(
            allowed=count <= limit,
            limit=limit,
            remaining=max(0, limit - count),
            retry_after=window_start + window_seconds - now,
        )
