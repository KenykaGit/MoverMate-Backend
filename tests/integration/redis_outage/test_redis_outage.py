"""Redis is auxiliary: while it is down, the service must keep working (fail-open),
report `degraded` and make the outage visible in logs and metrics."""

import pytest
from pydantic import SecretStr
from redis.asyncio import Redis

from app.application.interfaces.idempotency import BeginOutcome
from app.config import RateLimitSettings, RedisSettings
from app.infrastructure.redis.client import create_redis
from app.infrastructure.redis.idempotency import RedisIdempotencyStore
from app.infrastructure.redis.rate_limit import RedisRateLimiter
from tests.conftest import LogEvents
from tests.integration.conftest import UNREACHABLE_REDIS_URL, ClientFactory
from tests.integration.helpers import API, signed_up
from tests.routes import ECHO_PATH, add_test_routes

BROKEN_REDIS = RedisSettings(url=SecretStr(UNREACHABLE_REDIS_URL), timeout_seconds=0.2)


@pytest.fixture
async def broken_redis() -> Redis:
    return create_redis(UNREACHABLE_REDIS_URL, timeout_seconds=0.2)


async def test_main_scenario_survives_a_redis_outage(
    client_factory: ClientFactory, log_events: LogEvents
) -> None:
    async with client_factory(
        redis=BROKEN_REDIS,
        # Would block the third request of a scope if the limiter were working.
        rate_limit=RateLimitSettings(api_per_minute=2, auth_per_minute=2),
        configure_app=add_test_routes,
    ) as client:
        session = await signed_up(client)
        refreshed = await client.post(
            f"{API}/auth/refresh", json={"refresh_token": session.refresh_token}
        )
        assert refreshed.status_code == 200, refreshed.text
        access = refreshed.json()["access_token"]

        me = await client.get(f"{API}/users/me", headers={"Authorization": f"Bearer {access}"})
        assert me.status_code == 200
        assert "X-RateLimit-Limit" not in me.headers  # limiter skipped, not enforced

        idempotent = await client.post(
            ECHO_PATH, json={"text": "offline"}, headers={"Idempotency-Key": "outage-key-1"}
        )
        assert idempotent.status_code == 200  # runs without idempotency

        ready = await client.get("/health/ready")
        metrics = (await client.get("/metrics")).text

    assert ready.status_code == 200, "a Redis outage must not take the instance out"
    assert ready.json()["status"] == "degraded"
    assert ready.json()["checks"] == {"postgres": "ok", "redis": "degraded"}
    for op in ("rate_limit", "idempotency_begin"):
        assert f'redis_errors_total{{op="{op}"}}' in metrics
    failed_ops = {e["op"] for e in log_events() if e["event"] == "redis_unavailable"}
    assert {"rate_limit", "idempotency_begin"} <= failed_ops


async def test_adapters_fail_open(broken_redis: Redis) -> None:
    store = RedisIdempotencyStore(broken_redis, pending_ttl_seconds=30, completed_ttl_seconds=60)

    assert (await store.begin("k", "fp")).outcome is BeginOutcome.UNAVAILABLE
    await store.complete("k", "fp", {"id": 1})
    await store.release("k")
    assert await RedisRateLimiter(broken_redis).hit("k", limit=1, window_seconds=60) is None
    await broken_redis.aclose()
