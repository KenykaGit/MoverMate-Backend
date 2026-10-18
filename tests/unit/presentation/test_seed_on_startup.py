"""Seeding at startup: the manual container call in create_app, without Docker."""

from collections.abc import AsyncIterator
from dataclasses import replace

import pytest
from dishka import AsyncContainer
from dishka.integrations.fastapi import FastapiProvider
from pydantic import SecretStr

from app.application.seed.seed_test_users import SeedTestUsers
from app.config import DatabaseSettings, Environment, Settings
from app.ioc.config_provider import http_settings_from
from app.ioc.container import create_container
from app.presentation.http.app import create_app, seed_test_users
from tests.conftest import LogEvents

DEAD_DATABASE = DatabaseSettings(url=SecretStr("postgresql+asyncpg://app:app@127.0.0.1:1/none"))


@pytest.fixture
async def container() -> AsyncIterator[AsyncContainer]:
    settings = Settings(_env_file=None, database=DEAD_DATABASE)
    container = create_container(settings, FastapiProvider())
    yield container
    await container.close()


async def test_the_use_case_resolves_in_a_hand_made_request_scope(
    container: AsyncContainer,
) -> None:
    async with container() as scope:
        assert isinstance(await scope.get(SeedTestUsers), SeedTestUsers)


async def test_a_failing_seed_is_logged_and_does_not_stop_the_start(
    container: AsyncContainer, log_events: LogEvents
) -> None:
    await seed_test_users(container)

    assert [e["event"] for e in log_events() if e["event"] == "test_users_seed_failed"] == [
        "test_users_seed_failed"
    ]


async def test_startup_seeds_only_when_asked_to(
    container: AsyncContainer, log_events: LogEvents
) -> None:
    settings = http_settings_from(Settings(_env_file=None))
    assert settings.seed_test_users is False
    app = create_app(container, settings)

    async with app.router.lifespan_context(app):
        pass

    assert "test_users_seed_failed" not in [e["event"] for e in log_events()]


async def test_startup_runs_the_seed_when_enabled(
    container: AsyncContainer, log_events: LogEvents
) -> None:
    settings = replace(http_settings_from(Settings(_env_file=None)), seed_test_users=True)
    app = create_app(container, settings)

    async with app.router.lifespan_context(app):
        pass

    assert "test_users_seed_failed" in [e["event"] for e in log_events()]


def test_the_flag_is_refused_in_prod() -> None:
    with pytest.raises(ValueError, match="SEED_TEST_USERS"):
        Settings(
            _env_file=None,
            env=Environment.PROD,
            seed_test_users=True,
            auth={"secret_key": "p" * 40},
        )
