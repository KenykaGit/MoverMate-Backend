import pytest

from app.application.exceptions import UsernameAlreadyTakenError
from app.application.users.dto import RegisterUserDTO
from app.application.users.register_user import RegisterUser
from app.domain.common.errors import DomainError
from app.domain.common.ids import UserId
from app.domain.users.value_objects import Username
from tests.conftest import LogEvents
from tests.factories import make_user
from tests.fakes import FakePasswordHasher, FakeUnitOfWork, InMemoryUserRepository

PASSWORD = "correct horse"


@pytest.fixture
def register(
    users: InMemoryUserRepository, hasher: FakePasswordHasher, uow: FakeUnitOfWork
) -> RegisterUser:
    return RegisterUser(users, hasher, uow)


def dto(username: str = "John_Doe") -> RegisterUserDTO:
    return RegisterUserDTO(username=username, password=PASSWORD)


async def test_registers_active_user_with_normalized_username(
    register: RegisterUser, users: InMemoryUserRepository, uow: FakeUnitOfWork
) -> None:
    result = await register(dto())

    assert result.username == "john_doe"
    stored = users.users[UserId(result.id)]
    assert stored.is_active
    assert stored.username == Username("john_doe")
    assert uow.commits == 1


async def test_stores_only_the_hash(register: RegisterUser, users: InMemoryUserRepository) -> None:
    result = await register(dto())

    assert users.users[UserId(result.id)].password_hash == FakePasswordHasher.hash_of(PASSWORD)


async def test_rejects_taken_username_before_hashing(
    register: RegisterUser,
    users: InMemoryUserRepository,
    hasher: FakePasswordHasher,
    uow: FakeUnitOfWork,
) -> None:
    await users.add(make_user(username="john_doe"))

    with pytest.raises(UsernameAlreadyTakenError):
        await register(dto(username="JOHN_DOE"))

    assert hasher.hash_calls == 0
    assert uow.commits == 0


async def test_username_of_a_deleted_user_is_free(
    register: RegisterUser, users: InMemoryUserRepository
) -> None:
    await users.add(make_user(username="john_doe", is_active=False))

    result = await register(dto())

    assert await users.get_by_username(Username("john_doe")) is not None
    assert len(users.users) == 2
    assert result.username == "john_doe"


@pytest.mark.parametrize(
    "data",
    [
        RegisterUserDTO(username="j", password=PASSWORD),
        RegisterUserDTO(username="john@example.com", password=PASSWORD),
        RegisterUserDTO(username="john_doe", password="short"),
    ],
)
async def test_rejects_invalid_values(
    register: RegisterUser, uow: FakeUnitOfWork, data: RegisterUserDTO
) -> None:
    with pytest.raises(DomainError):
        await register(data)

    assert uow.commits == 0


async def test_logs_audit_event_without_secrets(
    register: RegisterUser, log_events: LogEvents
) -> None:
    result = await register(dto())

    [event] = [e for e in log_events() if e["event"] == "user_registered"]
    assert event["user_id"] == str(result.id)
    assert PASSWORD not in str(log_events())
