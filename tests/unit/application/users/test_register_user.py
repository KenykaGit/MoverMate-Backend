import pytest

from app.application.exceptions import EmailAlreadyTakenError, UsernameAlreadyTakenError
from app.application.users.dto import RegisterUserDTO
from app.application.users.register_user import RegisterUser
from app.domain.common.errors import DomainError
from app.domain.common.ids import UserId
from app.domain.users.value_objects import Email, Username
from tests.conftest import LogEvents
from tests.factories import make_user
from tests.fakes import FakePasswordHasher, FakeUnitOfWork, InMemoryUserRepository

PASSWORD = "correct horse"


@pytest.fixture
def register(
    users: InMemoryUserRepository, hasher: FakePasswordHasher, uow: FakeUnitOfWork
) -> RegisterUser:
    return RegisterUser(users, hasher, uow)


def dto(email: str = "John@Example.com", username: str = "John_Doe") -> RegisterUserDTO:
    return RegisterUserDTO(email=email, username=username, password=PASSWORD)


async def test_registers_active_user_with_normalized_values(
    register: RegisterUser, users: InMemoryUserRepository, uow: FakeUnitOfWork
) -> None:
    result = await register(dto())

    assert (result.email, result.username) == ("john@example.com", "john_doe")
    stored = users.users[UserId(result.id)]
    assert stored.is_active
    assert stored.email == Email("john@example.com")
    assert uow.commits == 1


async def test_stores_only_the_hash(register: RegisterUser, users: InMemoryUserRepository) -> None:
    result = await register(dto())

    assert users.users[UserId(result.id)].password_hash == FakePasswordHasher.hash_of(PASSWORD)


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (dto(email="john@example.com", username="other"), EmailAlreadyTakenError),
        (dto(email="other@example.com", username="JOHN_DOE"), UsernameAlreadyTakenError),
    ],
)
async def test_rejects_taken_values_before_hashing(
    register: RegisterUser,
    users: InMemoryUserRepository,
    hasher: FakePasswordHasher,
    uow: FakeUnitOfWork,
    data: RegisterUserDTO,
    error: type[Exception],
) -> None:
    await users.add(make_user(email="john@example.com", username="john_doe"))

    with pytest.raises(error):
        await register(data)

    assert hasher.hash_calls == 0
    assert uow.commits == 0


async def test_values_of_deleted_users_are_free(
    register: RegisterUser, users: InMemoryUserRepository
) -> None:
    await users.add(make_user(email="john@example.com", username="john_doe", is_active=False))

    result = await register(dto())

    assert await users.get_by_username(Username("john_doe")) is not None
    assert len(users.users) == 2
    assert result.username == "john_doe"


@pytest.mark.parametrize(
    "data",
    [
        RegisterUserDTO(email="not-an-email", username="john_doe", password=PASSWORD),
        RegisterUserDTO(email="john@example.com", username="j", password=PASSWORD),
        RegisterUserDTO(email="john@example.com", username="john_doe", password="short"),
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
