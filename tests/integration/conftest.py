"""Integration fixtures: real PostgreSQL and Redis.

By default both run in testcontainers (Docker required). CI and `make test-local`
point at existing servers instead: TEST_DATABASE_URL (database name must end in
`_test`) and TEST_REDIS_URL (logical DB 15), because tests TRUNCATE and FLUSHDB.
"""

import os
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from dishka.integrations.fastapi import FastapiProvider
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from app.config import (
    AuthSettings,
    DatabaseSettings,
    Environment,
    PushSettings,
    RateLimitSettings,
    RedisSettings,
    Settings,
)
from app.infrastructure.db.models import Base
from app.ioc.config_provider import http_settings_from
from app.ioc.container import create_container
from app.presentation.http.app import create_app
from tests.integration.llm_service import FakeLlmService
from tests.integration.push_service import FakePushService

PROJECT_ROOT = Path(__file__).parents[2]
# The same images as docker-compose.yml and CI.
POSTGRES_IMAGE = "postgres:18.6-alpine3.24"
REDIS_IMAGE = "valkey/valkey:9.1.2-alpine3.24"
TEST_SECRET_KEY = "test-secret-key-that-is-long-enough-for-hs256"
# Points at a closed port: every Redis call fails fast, like during an outage.
UNREACHABLE_REDIS_URL = "redis://127.0.0.1:1/15"

type ClientFactory = Callable[..., AbstractAsyncContextManager[AsyncClient]]


def alembic_config(database_url: str) -> AlembicConfig:
    config = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        name = make_url(explicit).database or ""
        if not name.endswith("_test"):
            pytest.exit(f"Refusing to run: database {name!r} does not end with '_test'")
        yield explicit
        return
    with PostgresContainer(POSTGRES_IMAGE, driver="asyncpg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    explicit = os.environ.get("TEST_REDIS_URL")
    if explicit:
        if not explicit.endswith("/15"):
            pytest.exit("Refusing to run: TEST_REDIS_URL must use logical DB 15 (…/15)")
        yield explicit
        return
    with RedisContainer(REDIS_IMAGE) as container:
        host = container.get_container_host_ip()
        yield f"redis://{host}:{container.get_exposed_port(6379)}/15"


@pytest.fixture(scope="session")
def migrated_postgres_url(postgres_url: str) -> str:
    """The schema comes from the real migrations, so they are tested too.

    A sync fixture: env.py calls asyncio.run(), which needs no running loop.
    """
    command.upgrade(alembic_config(postgres_url), "head")
    return postgres_url


@pytest.fixture(scope="session")
async def engine(migrated_postgres_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_postgres_url)
    await _truncate_all(engine)  # leftovers of an interrupted run on an external DB
    yield engine
    await engine.dispose()


async def _truncate_all(engine: AsyncEngine) -> None:
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
async def session_maker(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Session factory for one test; all tables are emptied afterwards."""
    yield async_sessionmaker(engine, expire_on_commit=False)
    await _truncate_all(engine)


@pytest.fixture
async def session(
    session_maker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_maker() as session:
        yield session


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client = Redis.from_url(redis_url, decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture(scope="session")
def vapid_key_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Where the app under test keeps its VAPID key: never in the working tree."""
    return tmp_path_factory.mktemp("vapid") / "private_key.pem"


@pytest.fixture
def make_settings(
    migrated_postgres_url: str, redis_url: str, vapid_key_file: Path
) -> Callable[..., Settings]:
    """Settings of the app under test; keyword arguments replace whole sections."""

    def factory(**overrides: Any) -> Settings:
        sections: dict[str, Any] = {
            "env": Environment.TEST,
            "app_version": "test-version",
            "log_json": False,
            "database": DatabaseSettings(url=SecretStr(migrated_postgres_url), pool_size=5),
            "redis": RedisSettings(url=SecretStr(redis_url)),
            # Cheap argon2: the real parameters cost ~50 ms and 64 MiB per hash.
            "auth": AuthSettings(
                secret_key=SecretStr(TEST_SECRET_KEY),
                argon2_time_cost=1,
                argon2_memory_cost_kib=8,
                argon2_parallelism=1,
            ),
            # Enabled explicitly by the rate limit tests.
            "rate_limit": RateLimitSettings(api_per_minute=0, auth_per_minute=0, scan_per_minute=0),
            "push": PushSettings(vapid_key_file=vapid_key_file),
        }
        # Never read a developer's .env or environment: tests must be reproducible.
        return Settings.model_construct(**(sections | overrides))

    return factory


@pytest.fixture
def client_factory(
    make_settings: Callable[..., Settings],
    session_maker: async_sessionmaker[AsyncSession],  # cleans the tables afterwards
    redis: Redis,  # flushes Redis before and after
) -> ClientFactory:
    """`async with client_factory(**settings_overrides) as client:` — the real app:
    real container, PostgreSQL, Redis and argon2, driven through ASGI."""

    @asynccontextmanager
    async def factory(
        *,
        raise_app_exceptions: bool = True,
        configure_app: Callable[[FastAPI], None] | None = None,
        **overrides: Any,
    ) -> AsyncIterator[AsyncClient]:
        settings = make_settings(**overrides)
        container = create_container(settings, FastapiProvider())
        app = create_app(container, http_settings_from(settings))
        if configure_app is not None:
            configure_app(app)
        transport = ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
        await container.close()  # ASGITransport does not run the lifespan

    return factory


@pytest.fixture
async def client(client_factory: ClientFactory) -> AsyncIterator[AsyncClient]:
    async with client_factory() as client:
        yield client


@pytest.fixture
async def push_service(tmp_path: Path) -> AsyncIterator[FakePushService]:
    """A Web Push service over HTTPS on localhost; see tests/integration/push_service.py."""
    service = FakePushService(tmp_path)
    await service.start()
    yield service
    await service.stop()


@pytest.fixture
async def llm_service() -> AsyncIterator[FakeLlmService]:
    """A Chat Completions API on localhost; see tests/integration/llm_service.py."""
    service = FakeLlmService()
    await service.start()
    yield service
    await service.stop()


@pytest.fixture
async def push_session(push_service: FakePushService) -> AsyncIterator[aiohttp.ClientSession]:
    """The HTTP session a WebPushSender needs to reach `push_service`."""
    session = push_service.client_session()
    yield session
    await session.close()
