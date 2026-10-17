"""The dependency graph: what is wired to what, with which lifetime and settings.

Building the container and resolving objects opens no connection (engine, Redis
and sessions connect lazily), so these tests need no Docker. What the wiring
does against a real database is in tests/integration/ioc/.
"""

import importlib
import inspect
import pkgutil
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import jwt
import pytest
from dishka import AsyncContainer, make_async_container
from dishka.exceptions import GraphMissingFactoryError
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import app.application
from app.application.auth.dto import RefreshTokenSettings
from app.application.auth.services import CredentialsVerifier, TokenPairIssuer
from app.application.common.idempotency import IdempotentExecutor
from app.application.common.uow import AbstractUnitOfWork
from app.application.health.check_readiness import CheckReadiness
from app.application.health.dto import ReadinessProbes
from app.application.interfaces.idempotency import AbstractIdempotencyStore
from app.application.interfaces.password_hasher import AbstractPasswordHasher
from app.application.interfaces.push_sender import AbstractPushSender
from app.application.interfaces.rate_limiter import AbstractRateLimiter
from app.application.interfaces.refresh_token_repository import AbstractRefreshTokenRepository
from app.application.interfaces.tokens import (
    AbstractAccessTokenService,
    AbstractRefreshTokenGenerator,
)
from app.application.interfaces.user_repository import AbstractUserRepository
from app.application.projects.services import MovingReminderScheduler
from app.application.users.register_user import RegisterUser
from app.config import AuthSettings, PushSettings, RateLimitSettings, Settings
from app.domain.common.ids import UserId, new_id
from app.domain.users.value_objects import RawPassword
from app.infrastructure.db.repositories.refresh_token import SqlAlchemyRefreshTokenRepository
from app.infrastructure.db.repositories.user import SqlAlchemyUserRepository
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.infrastructure.push.sender import WebPushSender
from app.ioc.config_provider import ConfigProvider
from app.ioc.container import create_container
from app.ioc.db_provider import DatabaseProvider
from app.ioc.observability_provider import ObservabilityProvider
from app.ioc.redis_provider import RedisProvider
from app.ioc.use_cases_provider import UseCaseProvider
from app.presentation.http.settings import HttpSettings, RateLimitPolicy

SECRET = SecretStr("wiring-test-secret-key-that-is-long-enough")


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_name="wiring-test",
        app_version="1.2.3",
        cors_origins=["https://app.example.com"],
        auth=AuthSettings(
            secret_key=SECRET,
            access_token_ttl=timedelta(minutes=5),
            refresh_token_ttl=timedelta(days=7),
            # Cheap argon2, and distinctive: the hashes below must carry these values.
            argon2_time_cost=1,
            argon2_memory_cost_kib=8,
            argon2_parallelism=1,
        ),
        rate_limit=RateLimitSettings(api_per_minute=42, auth_per_minute=7),
        push=PushSettings(vapid_key_file=Path("/nonexistent/wiring-test/vapid.pem")),
    )


@pytest.fixture
async def container(settings: Settings) -> AsyncIterator[AsyncContainer]:
    container = create_container(settings)
    yield container
    await container.close()


def _use_cases() -> list[type]:
    """Every use case class in app.application: a concrete class with `async __call__`."""
    found = set()
    for info in pkgutil.walk_packages(app.application.__path__, "app.application."):
        module = importlib.import_module(info.name)
        for cls in vars(module).values():
            if (
                inspect.isclass(cls)
                and cls.__module__ == module.__name__
                and not inspect.isabstract(cls)
                and inspect.iscoroutinefunction(cls.__call__)
            ):
                found.add(cls)
    return sorted(found, key=lambda cls: cls.__qualname__)


class TestGraph:
    def test_is_validated_when_the_container_is_built(self, settings: Settings) -> None:
        """A forgotten adapter fails at startup, not on the first request that needs it."""
        without_security = (
            ConfigProvider(),
            DatabaseProvider(),
            RedisProvider(),
            UseCaseProvider(),
            ObservabilityProvider(),
        )

        with pytest.raises(GraphMissingFactoryError, match="AbstractPasswordHasher"):
            make_async_container(*without_security, context={Settings: settings})

    def test_use_cases_are_discovered(self) -> None:
        assert len(_use_cases()) >= 10, "the discovery below must not pass vacuously"

    @pytest.mark.parametrize("use_case", _use_cases(), ids=lambda cls: cls.__qualname__)
    async def test_every_use_case_is_provided(
        self, container: AsyncContainer, use_case: type
    ) -> None:
        """A new use case class that nobody registered in ioc/ fails here."""
        async with container() as request:
            assert isinstance(await request.get(use_case), use_case)

    @pytest.mark.parametrize(
        "service",
        [
            CredentialsVerifier,
            TokenPairIssuer,
            IdempotentExecutor,
            CheckReadiness,
            MovingReminderScheduler,
        ],
    )
    async def test_application_services_are_provided(
        self, container: AsyncContainer, service: type
    ) -> None:
        async with container() as request:
            assert isinstance(await request.get(service), service)


class TestScopes:
    async def test_repositories_and_unit_of_work_share_the_request_session(
        self, container: AsyncContainer
    ) -> None:
        async with container() as request:
            session = await request.get(AsyncSession)
            users = await request.get(AbstractUserRepository)
            tokens = await request.get(AbstractRefreshTokenRepository)
            uow = await request.get(AbstractUnitOfWork)

        assert isinstance(users, SqlAlchemyUserRepository)
        assert isinstance(tokens, SqlAlchemyRefreshTokenRepository)
        assert isinstance(uow, SqlAlchemyUnitOfWork)
        assert users._session is tokens._session is uow._session is session

    async def test_each_request_gets_its_own_session_and_use_cases(
        self, container: AsyncContainer
    ) -> None:
        async with container() as first:
            first_objects = [await first.get(t) for t in (AsyncSession, RegisterUser)]
        async with container() as second:
            second_objects = [await second.get(t) for t in (AsyncSession, RegisterUser)]

        for one, other in zip(first_objects, second_objects, strict=True):
            assert one is not other

    @pytest.mark.parametrize(
        "dependency",
        [
            AsyncEngine,
            async_sessionmaker[AsyncSession],
            Redis,
            AbstractPasswordHasher,
            AbstractAccessTokenService,
            AbstractRefreshTokenGenerator,
            AbstractRateLimiter,
            AbstractIdempotencyStore,
            CheckReadiness,
        ],
    )
    async def test_process_wide_objects_are_shared_by_all_requests(
        self, container: AsyncContainer, dependency: object
    ) -> None:
        """One engine and one Redis pool per process; a hasher keeps its dummy hash."""
        async with container() as first:
            one = await first.get(dependency)
        async with container() as second:
            other = await second.get(dependency)

        assert one is other


class TestSettingsReachTheAdapters:
    async def test_password_hasher_uses_the_argon2_parameters(
        self, container: AsyncContainer
    ) -> None:
        hasher = await container.get(AbstractPasswordHasher)

        password_hash = await hasher.hash(RawPassword("correct horse"))

        assert password_hash.value.startswith("$argon2id$v=19$m=8,t=1,p=1$")

    async def test_access_tokens_use_the_secret_issuer_and_ttl(
        self, container: AsyncContainer
    ) -> None:
        tokens = await container.get(AbstractAccessTokenService)

        claims = jwt.decode(
            tokens.issue(UserId(new_id())).token,
            SECRET.get_secret_value(),
            algorithms=["HS256"],
            issuer="wiring-test",
        )

        assert claims["exp"] - claims["iat"] == 5 * 60

    async def test_plain_settings_objects_for_the_inner_layers(
        self, container: AsyncContainer
    ) -> None:
        assert await container.get(RefreshTokenSettings) == RefreshTokenSettings(
            ttl=timedelta(days=7)
        )
        assert await container.get(RateLimitPolicy) == RateLimitPolicy(
            api_per_window=42, auth_per_window=7
        )
        assert await container.get(HttpSettings) == HttpSettings(
            title="wiring-test", version="1.2.3", cors_origins=("https://app.example.com",)
        )

    async def test_postgres_is_critical_redis_and_the_notification_queue_auxiliary(
        self, container: AsyncContainer
    ) -> None:
        probes = await container.get(ReadinessProbes)

        assert (set(probes.critical), set(probes.auxiliary)) == (
            {"postgres"},
            {"redis", "notifications"},
        )

    async def test_push_sender_has_no_side_effects_until_it_sends(
        self, container: AsyncContainer, settings: Settings
    ) -> None:
        """The api resolves the same graph: building the sender must not create
        the VAPID key file nor open connections."""
        sender = await container.get(AbstractPushSender)

        assert isinstance(sender, WebPushSender)
        assert sender._vapid is None
        assert sender._session is None
        assert not settings.push.vapid_key_file.exists()
