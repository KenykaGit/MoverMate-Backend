import pytest

from app.application.exceptions import UsernameAlreadyTakenError
from app.application.users.change_username import ChangeUsername
from app.application.users.dto import ChangeUsernameDTO
from app.domain.users.entity import User
from app.domain.users.errors import InvalidUsernameError
from app.domain.users.value_objects import Username
from tests.factories import make_user
from tests.fakes import FakePasswordHasher, FakeUnitOfWork, InMemoryUserRepository

PASSWORD = "the password"


@pytest.fixture
def user(users: InMemoryUserRepository) -> User:
    user = make_user(password_hash=FakePasswordHasher.hash_of(PASSWORD).value)
    users.users[user.id] = user
    return user


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
        await users.add(make_user(username="taken"))

        with pytest.raises(UsernameAlreadyTakenError):
            await change(ChangeUsernameDTO(user_id=user.id, new_username="taken"))

    async def test_rejects_malformed_username(self, change: ChangeUsername, user: User) -> None:
        with pytest.raises(InvalidUsernameError):
            await change(ChangeUsernameDTO(user_id=user.id, new_username="a@b"))
