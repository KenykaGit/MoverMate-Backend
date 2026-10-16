import pytest

from app.application.exceptions import InvalidCredentialsError, UserNotFoundError
from app.application.users.change_password import ChangePassword
from app.application.users.dto import ChangePasswordDTO
from app.domain.users.entity import User
from app.domain.users.errors import InvalidPasswordError
from tests.factories import make_refresh_token, make_user
from tests.fakes import (
    FakePasswordHasher,
    FakeUnitOfWork,
    InMemoryRefreshTokenRepository,
    InMemoryUserRepository,
)

OLD, NEW = "old password", "new password"


@pytest.fixture
def user(users: InMemoryUserRepository) -> User:
    user = make_user(password_hash=FakePasswordHasher.hash_of(OLD).value)
    users.users[user.id] = user
    return user


@pytest.fixture
def change(
    users: InMemoryUserRepository,
    refresh_tokens: InMemoryRefreshTokenRepository,
    hasher: FakePasswordHasher,
    uow: FakeUnitOfWork,
) -> ChangePassword:
    return ChangePassword(users, refresh_tokens, hasher, uow)


async def test_changes_hash_and_ends_every_session(
    change: ChangePassword,
    users: InMemoryUserRepository,
    refresh_tokens: InMemoryRefreshTokenRepository,
    uow: FakeUnitOfWork,
    user: User,
) -> None:
    await refresh_tokens.add(make_refresh_token(user.id))
    await refresh_tokens.add(make_refresh_token(user.id))

    await change(ChangePasswordDTO(user_id=user.id, current_password=OLD, new_password=NEW))

    assert users.users[user.id].password_hash == FakePasswordHasher.hash_of(NEW)
    assert refresh_tokens.active_for(user.id) == []
    assert uow.commits == 1


async def test_wrong_current_password(
    change: ChangePassword,
    users: InMemoryUserRepository,
    refresh_tokens: InMemoryRefreshTokenRepository,
    uow: FakeUnitOfWork,
    user: User,
) -> None:
    await refresh_tokens.add(make_refresh_token(user.id))

    with pytest.raises(InvalidCredentialsError):
        await change(ChangePasswordDTO(user_id=user.id, current_password="wrong", new_password=NEW))

    assert users.users[user.id].password_hash == FakePasswordHasher.hash_of(OLD)
    assert len(refresh_tokens.active_for(user.id)) == 1
    assert uow.commits == 0


async def test_new_password_must_satisfy_policy(
    change: ChangePassword, hasher: FakePasswordHasher, user: User
) -> None:
    with pytest.raises(InvalidPasswordError):
        await change(ChangePasswordDTO(user_id=user.id, current_password=OLD, new_password="x"))

    assert hasher.verify_calls == 0


async def test_deleted_user(change: ChangePassword, users: InMemoryUserRepository) -> None:
    deleted = make_user(is_active=False)
    users.users[deleted.id] = deleted

    with pytest.raises(UserNotFoundError):
        await change(ChangePasswordDTO(user_id=deleted.id, current_password=OLD, new_password=NEW))
