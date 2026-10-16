import pytest

from app.application.exceptions import (
    EmailAlreadyTakenError,
    InvalidCredentialsError,
    UsernameAlreadyTakenError,
)
from app.application.users.change_email import ChangeEmail
from app.application.users.change_username import ChangeUsername
from app.application.users.dto import ChangeEmailDTO, ChangeUsernameDTO
from app.domain.users.entity import User
from app.domain.users.errors import InvalidEmailError, InvalidUsernameError
from app.domain.users.value_objects import Email, Username
from tests.factories import make_user
from tests.fakes import FakePasswordHasher, FakeUnitOfWork, InMemoryUserRepository

PASSWORD = "the password"


@pytest.fixture
def user(users: InMemoryUserRepository) -> User:
    user = make_user(password_hash=FakePasswordHasher.hash_of(PASSWORD).value)
    users.users[user.id] = user
    return user


class TestChangeEmail:
    @pytest.fixture
    def change(
        self, users: InMemoryUserRepository, hasher: FakePasswordHasher, uow: FakeUnitOfWork
    ) -> ChangeEmail:
        return ChangeEmail(users, hasher, uow)

    async def test_changes_normalized_email(
        self, change: ChangeEmail, users: InMemoryUserRepository, uow: FakeUnitOfWork, user: User
    ) -> None:
        result = await change(
            ChangeEmailDTO(user_id=user.id, new_email="New@Example.com", password=PASSWORD)
        )

        assert result.email == "new@example.com"
        assert users.users[user.id].email == Email("new@example.com")
        assert uow.commits == 1

    async def test_requires_password_before_checking_uniqueness(
        self, change: ChangeEmail, users: InMemoryUserRepository, user: User
    ) -> None:
        await users.add(make_user(email="taken@example.com", username="other"))

        with pytest.raises(InvalidCredentialsError):
            await change(
                ChangeEmailDTO(user_id=user.id, new_email="taken@example.com", password="wrong")
            )

    async def test_rejects_taken_email(
        self, change: ChangeEmail, users: InMemoryUserRepository, user: User
    ) -> None:
        await users.add(make_user(email="taken@example.com", username="other"))

        with pytest.raises(EmailAlreadyTakenError):
            await change(
                ChangeEmailDTO(user_id=user.id, new_email="taken@example.com", password=PASSWORD)
            )
        assert users.users[user.id].email == Email("john@example.com")

    async def test_rejects_malformed_email(self, change: ChangeEmail, user: User) -> None:
        with pytest.raises(InvalidEmailError):
            await change(ChangeEmailDTO(user_id=user.id, new_email="nope", password=PASSWORD))


class TestChangeUsername:
    @pytest.fixture
    def change(self, users: InMemoryUserRepository, uow: FakeUnitOfWork) -> ChangeUsername:
        return ChangeUsername(users, uow)

    async def test_changes_normalized_username(
        self, change: ChangeUsername, users: InMemoryUserRepository, user: User
    ) -> None:
        result = await change(ChangeUsernameDTO(user_id=user.id, new_username="New_Name"))

        assert result.username == "new_name"
        assert users.users[user.id].username == Username("new_name")

    async def test_rejects_taken_username(
        self, change: ChangeUsername, users: InMemoryUserRepository, user: User
    ) -> None:
        await users.add(make_user(email="other@example.com", username="taken"))

        with pytest.raises(UsernameAlreadyTakenError):
            await change(ChangeUsernameDTO(user_id=user.id, new_username="taken"))

    async def test_rejects_malformed_username(self, change: ChangeUsername, user: User) -> None:
        with pytest.raises(InvalidUsernameError):
            await change(ChangeUsernameDTO(user_id=user.id, new_username="a@b"))
