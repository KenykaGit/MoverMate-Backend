import pytest

from app.application.exceptions import UserNotFoundError
from app.application.users.get_user import GetUser
from tests.factories import make_user
from tests.fakes import InMemoryUserRepository


async def test_returns_active_user() -> None:
    user = make_user()

    result = await GetUser(InMemoryUserRepository([user]))(user.id)

    assert (result.id, result.username) == (user.id, "john_doe")


async def test_missing_user_is_not_found() -> None:
    with pytest.raises(UserNotFoundError):
        await GetUser(InMemoryUserRepository())(make_user().id)


async def test_deleted_user_is_not_found() -> None:
    deleted = make_user(is_active=False)

    with pytest.raises(UserNotFoundError):
        await GetUser(InMemoryUserRepository([deleted]))(deleted.id)
