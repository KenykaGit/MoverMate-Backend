import pytest

from app.application.auth.authenticate_user import AuthenticateUser
from app.application.auth.logout import Logout
from app.application.exceptions import NotAuthenticatedError
from app.application.users.get_user import GetUser
from app.domain.tokens.entity import new_token_family
from tests.factories import make_refresh_token, make_user
from tests.fakes import (
    FakeAccessTokenService,
    FakeRefreshTokenGenerator,
    FakeUnitOfWork,
    InMemoryRefreshTokenRepository,
    InMemoryUserRepository,
)


class TestLogout:
    @pytest.fixture
    def logout(
        self,
        refresh_tokens: InMemoryRefreshTokenRepository,
        refresh_token_generator: FakeRefreshTokenGenerator,
        uow: FakeUnitOfWork,
    ) -> Logout:
        return Logout(refresh_tokens, refresh_token_generator, uow)

    async def test_revokes_only_this_session(
        self,
        logout: Logout,
        refresh_tokens: InMemoryRefreshTokenRepository,
        refresh_token_generator: FakeRefreshTokenGenerator,
        uow: FakeUnitOfWork,
    ) -> None:
        user = make_user()
        this = make_refresh_token(user.id, token_hash=refresh_token_generator.hash("this"))
        other = make_refresh_token(user.id)
        await refresh_tokens.add(this)
        await refresh_tokens.add(other)

        await logout("this")

        assert [t.id for t in refresh_tokens.active_for(user.id)] == [other.id]
        assert uow.commits == 1

    async def test_a_rotated_token_still_ends_its_session(
        self,
        logout: Logout,
        refresh_tokens: InMemoryRefreshTokenRepository,
        refresh_token_generator: FakeRefreshTokenGenerator,
        uow: FakeUnitOfWork,
    ) -> None:
        """The client may hold a stale token after losing a refresh response."""
        user = make_user()
        family = new_token_family()
        stale = make_refresh_token(
            user.id,
            family_id=family,
            token_hash=refresh_token_generator.hash("stale"),
            revoked=True,
        )
        current = make_refresh_token(user.id, family_id=family)
        other_session = make_refresh_token(user.id)
        for token in (stale, current, other_session):
            await refresh_tokens.add(token)

        await logout("stale")

        assert [t.id for t in refresh_tokens.active_for(user.id)] == [other_session.id]
        assert uow.commits == 1

    async def test_unknown_token_or_ended_session_is_silently_ignored(
        self,
        logout: Logout,
        refresh_tokens: InMemoryRefreshTokenRepository,
        refresh_token_generator: FakeRefreshTokenGenerator,
        uow: FakeUnitOfWork,
    ) -> None:
        revoked = make_refresh_token(
            make_user().id, token_hash=refresh_token_generator.hash("old"), revoked=True
        )
        await refresh_tokens.add(revoked)

        await logout("unknown")
        await logout("old")

        assert uow.commits == 0


class TestAuthenticateUser:
    @pytest.fixture
    def authenticate(
        self, access_tokens: FakeAccessTokenService, users: InMemoryUserRepository
    ) -> AuthenticateUser:
        return AuthenticateUser(access_tokens, GetUser(users))

    async def test_returns_the_tokens_user(
        self,
        authenticate: AuthenticateUser,
        access_tokens: FakeAccessTokenService,
        users: InMemoryUserRepository,
    ) -> None:
        user = make_user()
        await users.add(user)

        result = await authenticate(access_tokens.issue(user.id).token)

        assert result.id == user.id

    async def test_invalid_token(self, authenticate: AuthenticateUser) -> None:
        for token in ("", "garbage", "access:not-a-uuid"):
            with pytest.raises(NotAuthenticatedError):
                await authenticate(token)

    async def test_deleted_user_is_rejected_although_the_token_is_valid(
        self,
        authenticate: AuthenticateUser,
        access_tokens: FakeAccessTokenService,
        users: InMemoryUserRepository,
    ) -> None:
        user = make_user()
        await users.add(user)
        token = access_tokens.issue(user.id).token
        user.soft_delete()
        await users.update(user)

        with pytest.raises(NotAuthenticatedError):
            await authenticate(token)
