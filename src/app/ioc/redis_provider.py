from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide
from redis.asyncio import Redis

from app.application.interfaces.idempotency import AbstractIdempotencyStore
from app.application.interfaces.rate_limiter import AbstractRateLimiter
from app.config import IdempotencySettings, RedisSettings
from app.infrastructure.redis.client import RedisHealthProbe, create_redis
from app.infrastructure.redis.idempotency import RedisIdempotencyStore
from app.infrastructure.redis.rate_limit import RedisRateLimiter


class RedisProvider(Provider):
    """One client (connection pool) per process; adapters are stateless."""

    scope = Scope.APP

    @provide
    async def redis(self, config: RedisSettings) -> AsyncIterator[Redis]:
        client = create_redis(config.url.get_secret_value(), timeout_seconds=config.timeout_seconds)
        yield client
        await client.aclose()

    @provide
    def idempotency_store(
        self, redis: Redis, config: IdempotencySettings
    ) -> AbstractIdempotencyStore:
        return RedisIdempotencyStore(
            redis,
            pending_ttl_seconds=config.pending_ttl_seconds,
            completed_ttl_seconds=config.completed_ttl_seconds,
        )

    @provide
    def rate_limiter(self, redis: Redis) -> AbstractRateLimiter:
        return RedisRateLimiter(redis)

    health_probe = provide(RedisHealthProbe)
