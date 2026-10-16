"""The real container against PostgreSQL: transactions per request, races, the
whole user lifecycle through use cases. The wiring itself is checked without
Docker in tests/unit/ioc/."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from dishka import AsyncContainer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.auth.dto import LoginDTO, TokenPairDTO
from app.application.auth.login import Login
from app.application.auth.logout import Logout
from app.application.auth.refresh_tokens import RefreshTokens
from app.application.common.uow import AbstractUnitOfWork
from app.application.exceptions import (
    EmailAlreadyTakenError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    UserNotFoundError,
)
from app.application.interfaces.user_repository import AbstractUserRepository
from app.application.users.change_email import ChangeEmail
from app.application.users.change_password import ChangePassword
from app.application.users.change_username import ChangeUsername
from app.application.users.dto import (
    ChangeEmailDTO,
    ChangePasswordDTO,
    ChangeUsernameDTO,
    RegisterUserDTO,
    UserDTO,
)
from app.application.users.get_user import GetUser
from app.application.users.register_user import RegisterUser
from app.application.users.restore_user import RestoreUser
from app.application.users.soft_delete_user import SoftDeleteUser
from app.config import Settings
from app.infrastructure.db.repositories.user import SqlAlchemyUserRepository
from app.ioc.container import create_container
from tests.factories import make_user

type SessionMaker = async_sessionmaker[AsyncSession]

PASSWORD = "correct horse battery staple"


@pytest.fixture
async def container(
    make_settings: Callable[..., Settings],
    session_maker: SessionMaker,  # empties the tables afterwards
    redis: Redis,  # flushes Redis before and after
) -> AsyncIterator[AsyncContainer]:
    container = create_container(make_settings())
    yield container
    await container.close()


async def run[T](
    container: AsyncContainer, use_case: type[Callable[..., Awaitable[T]]], *args: object
) -> T:
    """Run a use case in its own request scope, as one HTTP request would."""
    async with container() as request:
        result: T = await (await request.get(use_case))(*args)
    return result


class TestTransactions:
    async def test_commit_publishes_the_changes_of_the_request(
        self, container: AsyncContainer, session_maker: SessionMaker
    ) -> None:
        user = make_user()

        async with container() as request:
            await (await request.get(AbstractUserRepository)).add(user)
            await (await request.get(AbstractUnitOfWork)).commit()

        async with session_maker() as session:
            assert await SqlAlchemyUserRepository(session).get_by_id(user.id) == user

    async def test_uncommitted_changes_are_discarded_when_the_request_ends(
        self, container: AsyncContainer, session_maker: SessionMaker
    ) -> None:
        user = make_user()

        async with container() as request:
            await (await request.get(AbstractUserRepository)).add(user)  # flushed, not committed

        async with session_maker() as session:
            assert await SqlAlchemyUserRepository(session).get_by_id(user.id) is None

    async def test_a_failed_use_case_leaves_nothing_behind(
        self, container: AsyncContainer, session_maker: SessionMaker
    ) -> None:
        await run(
            container,
            RegisterUser,
            RegisterUserDTO(email="a@example.com", username="first", password=PASSWORD),
        )

        with pytest.raises(EmailAlreadyTakenError):
            await run(
                container,
                RegisterUser,
                RegisterUserDTO(email="A@Example.com", username="second", password=PASSWORD),
            )

        async with session_maker() as session:
            users = SqlAlchemyUserRepository(session)
            assert await users.get_by_email(make_user(email="a@example.com").email) is not None
            assert await users.get_by_username(make_user(username="second").username) is None

    async def test_concurrent_registrations_with_one_email(self, container: AsyncContainer) -> None:
        """Exactly one wins; the others get an application error, not a database one."""

        async def register(username: str) -> UserDTO:
            data = RegisterUserDTO(email="john@example.com", username=username, password=PASSWORD)
            return await run(container, RegisterUser, data)

        results = await asyncio.gather(
            *(register(f"user_{number}") for number in range(5)), return_exceptions=True
        )

        assert sum(isinstance(result, UserDTO) for result in results) == 1
        assert sum(isinstance(result, EmailAlreadyTakenError) for result in results) == 4


async def test_user_lifecycle_through_the_real_stack(container: AsyncContainer) -> None:
    """Every user and auth use case against PostgreSQL and argon2, each in its own request."""
    new_password = "a new correct horse battery"

    async def login(name: str, password: str) -> TokenPairDTO:
        return await run(container, Login, LoginDTO(login=name, password=password))

    async def refresh(token: str) -> TokenPairDTO:
        return await run(container, RefreshTokens, token)

    user = await run(
        container,
        RegisterUser,
        RegisterUserDTO(email="Иван@Пример.РФ", username="Ivan", password=PASSWORD),
    )
    assert (user.email, user.username) == ("иван@пример.рф", "ivan")

    # Two devices; a replayed token ends its own session only.
    laptop = await login("ivan", PASSWORD)
    phone = await login("иван@xn--e1afmkfd.xn--p1ai", PASSWORD)  # punycode: the same address
    rotated = await refresh(laptop.refresh_token)
    with pytest.raises(InvalidRefreshTokenError):
        await refresh(laptop.refresh_token)
    with pytest.raises(InvalidRefreshTokenError):
        await refresh(rotated.refresh_token)
    phone = await refresh(phone.refresh_token)

    # Logout ends the phone session; a new password ends every session.
    tablet = await login("ivan", PASSWORD)
    await run(container, Logout, phone.refresh_token)
    with pytest.raises(InvalidRefreshTokenError):
        await refresh(phone.refresh_token)
    await run(
        container,
        ChangePassword,
        ChangePasswordDTO(user_id=user.id, current_password=PASSWORD, new_password=new_password),
    )
    with pytest.raises(InvalidRefreshTokenError):
        await refresh(tablet.refresh_token)
    with pytest.raises(InvalidCredentialsError):
        await login("ivan", PASSWORD)

    await run(
        container,
        ChangeEmail,
        ChangeEmailDTO(user_id=user.id, new_email="ivan@example.com", password=new_password),
    )
    await run(container, ChangeUsername, ChangeUsernameDTO(user_id=user.id, new_username="ivan_2"))
    changed = await run(container, GetUser, user.id)
    assert (changed.email, changed.username) == ("ivan@example.com", "ivan_2")
    await login("ivan@example.com", new_password)

    # Soft delete frees the email; a restore conflicts while someone else holds it.
    await run(container, SoftDeleteUser, user.id)
    with pytest.raises(UserNotFoundError):
        await run(container, GetUser, user.id)
    with pytest.raises(InvalidCredentialsError):
        await login("ivan_2", new_password)
    newcomer = await run(
        container,
        RegisterUser,
        RegisterUserDTO(email="ivan@example.com", username="newcomer", password=PASSWORD),
    )
    with pytest.raises(EmailAlreadyTakenError):
        await run(container, RestoreUser, user.id)

    await run(container, SoftDeleteUser, newcomer.id)
    assert await run(container, RestoreUser, user.id) == changed
    await login("ivan_2", new_password)
