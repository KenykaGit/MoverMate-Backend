import pytest

from app.application.auth.dto import LoginDTO
from app.application.auth.login import Login
from app.application.auth.services import CredentialsVerifier, TokenPairIssuer
from app.application.exceptions import InvalidCredentialsError
from app.domain.users.entity import User
from tests.conftest import LogEvents
from tests.factories import make_user
from tests.fakes import (
    FakeAccessTokenService,
    FakePasswordHasher,
    FakeRefreshTokenGenerator,
    FakeUnitOfWork,
    InMemoryRefreshTokenRepository,
    InMemoryUserRepository,
)

PASSWORD = "the password"


@pytest.fixture
def user(users: InMemoryUserRepository) -> User:
    user = make_user(password_hash=FakePasswordHasher.hash_of(PASSWORD).value)
    users.users[user.id] = user
    return user


@pytest.fixture
def login(
    credentials_verifier: CredentialsVerifier,
    token_pair_issuer: TokenPairIssuer,
    uow: FakeUnitOfWork,
) -> Login:
    return Login(credentials_verifier, token_pair_issuer, uow)


async def test_issues_token_pair_by_username(
    login: Login,
    access_tokens: FakeAccessTokenService,
    refresh_tokens: InMemoryRefreshTokenRepository,
    refresh_token_generator: FakeRefreshTokenGenerator,
    uow: FakeUnitOfWork,
    user: User,
) -> None:
    usernames = ["john_doe", "John_Doe", "  JOHN_DOE "]  # normalized like at registration
    issued = [await login(LoginDTO(username=name, password=PASSWORD)) for name in usernames]

    assert [access_tokens.read_user_id(t.access_token) for t in issued] == [user.id] * 3
    stored = {token.token_hash for token in refresh_tokens.active_for(user.id)}
    # Only the hash of the refresh token is stored.
    assert stored == {refresh_token_generator.hash(t.refresh_token) for t in issued}
    assert stored.isdisjoint(t.refresh_token for t in issued)
    assert uow.commits == 3


async def test_each_login_is_a_separate_session(
    login: Login, refresh_tokens: InMemoryRefreshTokenRepository, user: User
) -> None:
    await login(LoginDTO(username="john_doe", password=PASSWORD))
    await login(LoginDTO(username="john_doe", password=PASSWORD))

    sessions = refresh_tokens.active_for(user.id)
    assert len(sessions) == 2
    assert sessions[0].family_id != sessions[1].family_id


class TestRehash:
    """A hash made with outdated parameters is upgraded on the next successful login."""

    @staticmethod
    async def seed(users: InMemoryUserRepository, password: str, *, outdated: bool) -> User:
        user = make_user(
            password_hash=FakePasswordHasher.hash_of(password, outdated=outdated).value
        )
        await users.add(user)
        return user

    async def test_outdated_hash_is_upgraded(
        self,
        login: Login,
        users: InMemoryUserRepository,
        uow: FakeUnitOfWork,
        log_events: LogEvents,
    ) -> None:
        user = await self.seed(users, PASSWORD, outdated=True)

        await login(LoginDTO(username="john_doe", password=PASSWORD))

        assert users.users[user.id].password_hash == FakePasswordHasher.hash_of(PASSWORD)
        assert uow.commits == 1, "the new hash is committed together with the session"
        [event] = [e for e in log_events() if e["event"] == "password_rehashed"]
        assert event["user_id"] == str(user.id)

    async def test_current_hash_is_left_alone(
        self, login: Login, users: InMemoryUserRepository, hasher: FakePasswordHasher
    ) -> None:
        user = await self.seed(users, PASSWORD, outdated=False)

        await login(LoginDTO(username="john_doe", password=PASSWORD))

        assert hasher.hash_calls == 0
        assert users.users[user.id].password_hash == FakePasswordHasher.hash_of(PASSWORD)

    async def test_password_set_under_an_older_policy_is_upgraded_too(
        self, login: Login, users: InMemoryUserRepository
    ) -> None:
        """The policy applies when a password is set, never at login."""
        user = await self.seed(users, "short", outdated=True)

        await login(LoginDTO(username="john_doe", password="short"))

        assert users.users[user.id].password_hash == FakePasswordHasher.hash_of("short")

    async def test_failed_login_changes_nothing(
        self, login: Login, users: InMemoryUserRepository, hasher: FakePasswordHasher
    ) -> None:
        user = await self.seed(users, PASSWORD, outdated=True)

        with pytest.raises(InvalidCredentialsError):
            await login(LoginDTO(username="john_doe", password="wrong password"))

        assert hasher.hash_calls == 0
        assert users.users[user.id].password_hash == FakePasswordHasher.hash_of(
            PASSWORD, outdated=True
        )


@pytest.mark.usefixtures("user")
async def test_wrong_password(
    login: Login, uow: FakeUnitOfWork, refresh_tokens: InMemoryRefreshTokenRepository
) -> None:
    with pytest.raises(InvalidCredentialsError):
        await login(LoginDTO(username="john_doe", password="wrong password"))

    assert refresh_tokens.tokens == {}
    assert uow.commits == 0


@pytest.mark.usefixtures("user")
async def test_unknown_username_costs_as_much_as_a_wrong_password(
    login: Login, hasher: FakePasswordHasher
) -> None:
    usernames = ["nobody", "@@", "x", "john@example.com"]  # an email is not a login
    for username in usernames:
        with pytest.raises(InvalidCredentialsError):
            await login(LoginDTO(username=username, password=PASSWORD))

    assert hasher.dummy_verify_calls == len(usernames)


async def test_deleted_user_cannot_log_in(
    login: Login, users: InMemoryUserRepository, hasher: FakePasswordHasher, user: User
) -> None:
    user.soft_delete()
    await users.update(user)

    with pytest.raises(InvalidCredentialsError):
        await login(LoginDTO(username="john_doe", password=PASSWORD))
    assert hasher.dummy_verify_calls == 1


@pytest.mark.usefixtures("user")
async def test_audit_events_never_contain_credentials(login: Login, log_events: LogEvents) -> None:
    with pytest.raises(InvalidCredentialsError):
        await login(LoginDTO(username="john_doe", password="wrong password"))
    with pytest.raises(InvalidCredentialsError):
        await login(LoginDTO(username="nobody", password="wrong password"))
    await login(LoginDTO(username="john_doe", password=PASSWORD))

    events = log_events()
    reasons = [e["reason"] for e in events if e["event"] == "login_failed"]
    assert reasons == ["wrong_password", "unknown_login"]
    assert [e["event"] for e in events].count("user_logged_in") == 1
    text = str(events)
    assert "john_doe" not in text
    assert "nobody" not in text
    assert "password" not in text.replace("wrong_password", "")
