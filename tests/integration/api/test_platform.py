"""Cross-cutting behaviour: health, metrics, request id, rate limits, error contract.

The published OpenAPI schema is checked in tests/unit/presentation/test_openapi.py."""

from collections.abc import Callable

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.main
from app.config import DatabaseSettings, RateLimitSettings, Settings
from tests.conftest import LogEvents
from tests.integration.conftest import ClientFactory
from tests.integration.helpers import API, PASSWORD, register, signed_up
from tests.routes import ECHO_PATH, ITEM_PATH, add_test_routes


async def test_live(client: AsyncClient) -> None:
    response = await client.get("/health/live")

    assert (response.status_code, response.json()) == (200, {"status": "ok"})


async def test_ready(client: AsyncClient) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": "test-version",
        "checks": {"postgres": "ok", "redis": "ok"},
    }


async def test_ready_without_postgres_takes_the_instance_out(
    client_factory: ClientFactory,
) -> None:
    dead = DatabaseSettings(url=SecretStr("postgresql+asyncpg://app:app@127.0.0.1:1/none_test"))
    async with client_factory(database=dead) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["checks"] == {"postgres": "unavailable", "redis": "ok"}


async def test_metrics_use_route_templates(client_factory: ClientFactory) -> None:
    item_id = "0191c6a2-7d0b-7c3e-9a1b-2f0e5d4c3b2a"
    async with client_factory(configure_app=add_test_routes) as client:
        session = await signed_up(client)
        await client.get(f"{API}/users/me", headers=session.headers)
        await client.get(f"{ITEM_PATH}/{item_id}")
        await client.get("/no/such/path")

        body = (await client.get("/metrics")).text

    assert 'route="/api/v1/users/me"' in body
    assert 'route="/api/v1/test/items/{item_id}"' in body
    assert item_id not in body  # bounded label cardinality
    assert 'route="unmatched"' in body
    assert "http_request_duration_seconds_bucket" in body


async def test_request_id_is_echoed_and_logged(client: AsyncClient, log_events: LogEvents) -> None:
    response = await client.get("/health/live", headers={"X-Request-ID": "trace-123"})

    assert response.headers["X-Request-ID"] == "trace-123"
    access = [e for e in log_events() if e["event"] == "http_request"]
    assert access[-1]["request_id"] == "trace-123"
    assert access[-1]["route"] == "/health/live"


async def test_unsafe_request_id_is_replaced(client: AsyncClient) -> None:
    response = await client.get("/health/live", headers={"X-Request-ID": "bad id\twith spaces"})

    assert response.headers["X-Request-ID"] != "bad id\twith spaces"
    assert len(response.headers["X-Request-ID"]) == 32


async def test_error_body_carries_the_request_id(client: AsyncClient) -> None:
    response = await client.get(f"{API}/users/me", headers={"X-Request-ID": "trace-401"})

    assert response.json()["error"]["request_id"] == "trace-401"


async def test_api_rate_limit(client_factory: ClientFactory) -> None:
    limits = RateLimitSettings(api_per_minute=3, auth_per_minute=0)
    async with client_factory(rate_limit=limits) as client:
        responses = [await client.get(f"{API}/users/me") for _ in range(3)]
        limited = await client.get(f"{API}/users/me")
        health = await client.get("/health/ready")

    assert [r.headers["X-RateLimit-Remaining"] for r in responses] == ["2", "1", "0"]
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert limited.headers["X-RateLimit-Limit"] == "3"
    assert 0 < int(limited.headers["Retry-After"]) <= 60
    assert health.status_code == 200, "monitoring is never throttled"


async def test_auth_rate_limit_is_stricter(client_factory: ClientFactory) -> None:
    limits = RateLimitSettings(api_per_minute=100, auth_per_minute=2)
    async with client_factory(rate_limit=limits) as client:
        await register(client)
        attempt = {"login": "john_doe", "password": "wrong password"}
        second = await client.post(f"{API}/auth/login", json=attempt)
        third = await client.post(f"{API}/auth/login", json={**attempt, "password": PASSWORD})
        me = await client.get(f"{API}/users/me")

    assert second.status_code == 401
    assert third.status_code == 429, "brute force is stopped even with the right password"
    assert me.status_code == 401  # other endpoints only count against the api scope
    assert me.headers["X-RateLimit-Remaining"] == "96"


async def test_idempotent_replay_keeps_the_rate_limit_headers(
    client_factory: ClientFactory,
) -> None:
    async with client_factory(
        rate_limit=RateLimitSettings(api_per_minute=10), configure_app=add_test_routes
    ) as client:
        headers = {"Idempotency-Key": "rate-limit-0001"}
        first = await client.post(ECHO_PATH, json={"text": "t"}, headers=headers)
        replay = await client.post(ECHO_PATH, json={"text": "t"}, headers=headers)
        other = await client.post(ECHO_PATH, json={"text": "u"}, headers=headers)

    assert first.json() == replay.json() == {"text": "t", "runs": 1}
    assert "Idempotent-Replayed" not in first.headers
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.headers["X-RateLimit-Remaining"] == "8"
    assert other.status_code == 422
    assert other.json()["error"]["code"] == "idempotency_key_reused"


async def test_routing_errors_use_the_contract(client: AsyncClient) -> None:
    not_found = await client.get("/api/v1/nothing")
    not_allowed = await client.delete("/health/live")

    assert not_found.status_code == 404
    assert not_found.json()["error"]["code"] == "not_found"
    assert not_allowed.status_code == 405
    assert not_allowed.json()["error"]["code"] == "method_not_allowed"


async def test_unexpected_errors_do_not_leak_internals(client_factory: ClientFactory) -> None:
    def add_failing_route(app: FastAPI) -> None:
        @app.get("/boom")
        async def boom() -> None:
            raise RuntimeError("secret internals")

    async with client_factory(raise_app_exceptions=False, configure_app=add_failing_route) as c:
        response = await c.get("/boom", headers={"X-Request-ID": "trace-500"})

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "Internal server error",
            "details": [],
            "request_id": "trace-500",
        }
    }
    assert response.headers["X-Request-ID"] == "trace-500"
    assert "secret" not in response.text


async def test_composition_root_serves_and_shuts_down(
    make_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """app.main.create_app, the entry point uvicorn runs, with its lifespan."""
    configured: list[tuple[str, bool]] = []
    # Logging is configured by the test session; record the call instead.
    monkeypatch.setattr(
        app.main, "setup_logging", lambda level, *, json_logs: configured.append((level, json_logs))
    )
    application = app.main.create_app(make_settings())

    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            ready = await client.get("/health/ready")

    assert ready.json()["status"] == "ok"
    assert configured == [("INFO", False)]
