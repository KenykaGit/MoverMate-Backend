import pytest
from redis.asyncio import Redis

from app.application.interfaces.idempotency import BeginOutcome, IdempotencyState
from app.infrastructure.redis.idempotency import RedisIdempotencyStore
from app.infrastructure.redis.rate_limit import RedisRateLimiter


class TestIdempotencyStore:
    @pytest.fixture
    def store(self, redis: Redis) -> RedisIdempotencyStore:
        return RedisIdempotencyStore(redis, pending_ttl_seconds=30, completed_ttl_seconds=3600)

    async def test_lifecycle(self, store: RedisIdempotencyStore, redis: Redis) -> None:
        started = await store.begin("idem:k", "fp")
        again = await store.begin("idem:k", "fp")

        assert started.outcome is BeginOutcome.STARTED
        assert again.outcome is BeginOutcome.EXISTS
        assert again.record is not None
        assert again.record.state is IdempotencyState.PENDING
        assert 0 < await redis.ttl("idem:k") <= 30

        await store.complete("idem:k", "fp", {"id": 1})
        replay = await store.begin("idem:k", "fp")

        assert replay.record is not None
        assert (replay.record.state, replay.record.response) == (
            IdempotencyState.COMPLETED,
            {"id": 1},
        )
        assert 30 < await redis.ttl("idem:k") <= 3600

    async def test_release_frees_the_key(self, store: RedisIdempotencyStore) -> None:
        await store.begin("idem:k", "fp")
        await store.release("idem:k")

        assert (await store.begin("idem:k", "fp")).outcome is BeginOutcome.STARTED

    async def test_corrupted_record_fails_open(
        self, store: RedisIdempotencyStore, redis: Redis
    ) -> None:
        await redis.set("idem:k", "not json")

        assert (await store.begin("idem:k", "fp")).outcome is BeginOutcome.UNAVAILABLE


class TestRateLimiter:
    async def test_fixed_window(self, redis: Redis) -> None:
        # Windows are aligned to multiples of 60 s: [960, 1020), [1020, 1080)...
        clock = iter([1000.0, 1001.0, 1002.0, 1020.0])
        limiter = RedisRateLimiter(redis, clock=lambda: next(clock))

        decisions = [await limiter.hit("api:1.2.3.4", limit=2, window_seconds=60) for _ in range(4)]

        assert [d.allowed for d in decisions if d] == [True, True, False, True]
        assert [d.remaining for d in decisions if d] == [1, 0, 0, 1]
        assert decisions[2] is not None
        assert decisions[2].retry_after == 18  # 1020 - 1002
        assert 0 < await redis.ttl("rl:api:1.2.3.4:960") <= 60

    async def test_keys_are_independent(self, redis: Redis) -> None:
        limiter = RedisRateLimiter(redis)

        first = await limiter.hit("auth:1.1.1.1", limit=1, window_seconds=60)
        other = await limiter.hit("auth:2.2.2.2", limit=1, window_seconds=60)

        assert first is not None
        assert other is not None
        assert first.allowed
        assert other.allowed
