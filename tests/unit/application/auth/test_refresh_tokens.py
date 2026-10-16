from datetime import datetime, timedelta

import pytest

from app.application.auth.refresh_tokens import RefreshTokens
from app.application.auth.services import TokenPairIssuer
from app.application.exceptions import InvalidRefreshTokenError
from app.domain.tokens.entity import RefreshTokenId, TokenFamilyId, new_token_family
from app.domain.users.entity import User
from tests.conftest import LogEvents
from tests.factories import make_refresh_token, make_user
from tests.fakes import (
    FakeAccessTokenService,
    FakeRefreshTokenGenerator,
    FakeUnitOfWork,
    InMemoryRefreshTokenRepository,
    InMemoryUserRepository,
)

TOKEN = "presented-token"


@pytest.fixture
def user(users: InMemoryUserRepository) -> User:
    user = make_user()
    users.users[user.id] = user
    return user


@pytest.fixture
def refresh(
    refresh_tokens: InMemoryRefreshTokenRepository,
    refresh_token_generator: FakeRefreshTokenGenerator,
    users: InMemoryUserRepository,
    token_pair_issuer: TokenPairIssuer,
    uow: FakeUnitOfWork,
) -> RefreshTokens:
    return RefreshTokens(refresh_tokens, refresh_token_generator, users, token_pair_issuer, uow)


async def store(
    refresh_tokens: InMemoryRefreshTokenRepository,
    generator: FakeRefreshTokenGenerator,
    user: User,
    *,
    token: str = TOKEN,
    family_id: TokenFamilyId | None = None,
    expires_in: timedelta = timedelta(days=1),
    revoked: bool = False,
) -> RefreshTokenId:
    stored = make_refresh_token(
        user.id,
        family_id=family_id,
        token_hash=generator.hash(token),
        expires_in=expires_in,
        revoked=revoked,
    )
    await refresh_tokens.add(stored)
    return stored.id


class TestRotation:
    async def test_issues_a_new_pair_and_revokes_the_presented_token(
        self,
        refresh: RefreshTokens,
        refresh_tokens: InMemoryRefreshTokenRepository,
        refresh_token_generator: FakeRefreshTokenGenerator,
        access_tokens: FakeAccessTokenService,
        uow: FakeUnitOfWork,
        user: User,
    ) -> None:
        old_id = await store(refresh_tokens, refresh_token_generator, user)

        tokens = await refresh(TOKEN)

        assert access_tokens.read_user_id(tokens.access_token) == user.id
        assert refresh_tokens.tokens[old_id].is_revoked
        [active] = refresh_tokens.active_for(user.id)
        assert active.token_hash == refresh_token_generator.hash(tokens.refresh_token)
        assert active.family_id == refresh_tokens.tokens[old_id].family_id, "same session"
        assert uow.commits == 1

    async def test_the_new_token_can_be_refreshed_again(
        self,
        refresh: RefreshTokens,
        refresh_tokens: InMemoryRefreshTokenRepository,
        refresh_token_generator: FakeRefreshTokenGenerator,
        user: User,
    ) -> None:
        await store(refresh_tokens, refresh_token_generator, user)

        first = await refresh(TOKEN)
        second = await refresh(first.refresh_token)

        assert second.refresh_token not in {TOKEN, first.refresh_token}


class TestRejection:
    async def test_unknown_token(self, refresh: RefreshTokens, uow: FakeUnitOfWork) -> None:
        with pytest.raises(InvalidRefreshTokenError):
            await refresh("unknown")
        assert uow.commits == 0

    async def test_expired_token(
        self,
        refresh: RefreshTokens,
        refresh_tokens: InMemoryRefreshTokenRepository,
        refresh_token_generator: FakeRefreshTokenGenerator,
        uow: FakeUnitOfWork,
        user: User,
    ) -> None:
        await store(refresh_tokens, refresh_token_generator, user, expires_in=-timedelta(seconds=1))

        with pytest.raises(InvalidRefreshTokenError):
            await refresh(TOKEN)
        assert uow.commits == 0

    async def test_deleted_user_and_the_token_stays_revoked(
        self,
        refresh: RefreshTokens,
        refresh_tokens: InMemoryRefreshTokenRepository,
        refresh_token_generator: FakeRefreshTokenGenerator,
        users: InMemoryUserRepository,
        user: User,
    ) -> None:
        token_id = await store(refresh_tokens, refresh_token_generator, user)
        user.soft_delete()
        await users.update(user)

        with pytest.raises(InvalidRefreshTokenError):
            await refresh(TOKEN)
        assert refresh_tokens.tokens[token_id].is_revoked


class TestReuseDetection:
    """A revoked token presented again has leaked: end its session (token family)."""

    async def test_replaying_a_rotated_token_revokes_its_family_only(
        self,
        refresh: RefreshTokens,
        refresh_tokens: InMemoryRefreshTokenRepository,
        refresh_token_generator: FakeRefreshTokenGenerator,
        uow: FakeUnitOfWork,
        user: User,
        log_events: LogEvents,
    ) -> None:
        family = new_token_family()
        await store(refresh_tokens, refresh_token_generator, user, family_id=family)
        other_device = await store(
            refresh_tokens, refresh_token_generator, user, token="other-device"
        )
        await refresh(TOKEN)  # the legitimate rotation

        with pytest.raises(InvalidRefreshTokenError):
            await refresh(TOKEN)  # the replay

        assert [t.id for t in refresh_tokens.active_for(user.id)] == [other_device]
        assert all(t.is_revoked for t in refresh_tokens.tokens.values() if t.family_id == family)
        assert uow.commits == 2  # the revocation is committed despite the error
        [event] = [e for e in log_events() if e["event"] == "refresh_token_reused"]
        assert event["level"] == "warning"
        assert (event["user_id"], event["family_id"]) == (str(user.id), str(family))

    async def test_other_users_are_not_affected(
        self,
        refresh: RefreshTokens,
        refresh_tokens: InMemoryRefreshTokenRepository,
        refresh_token_generator: FakeRefreshTokenGenerator,
        user: User,
    ) -> None:
        other = make_user(username="other")
        await store(refresh_tokens, refresh_token_generator, user, revoked=True)
        await store(refresh_tokens, refresh_token_generator, other, token="others")

        with pytest.raises(InvalidRefreshTokenError):
            await refresh(TOKEN)

        assert len(refresh_tokens.active_for(other.id)) == 1

    async def test_losing_a_concurrent_refresh_counts_as_reuse(
        self,
        refresh_token_generator: FakeRefreshTokenGenerator,
        users: InMemoryUserRepository,
        token_pair_issuer: TokenPairIssuer,
        uow: FakeUnitOfWork,
        user: User,
    ) -> None:
        """Another request revoked the token between our read and our revoke."""

        class RevokedInBetween(InMemoryRefreshTokenRepository):
            async def revoke(self, token_id: RefreshTokenId, now: datetime) -> bool:
                await super().revoke(token_id, now)  # the concurrent request wins
                return await super().revoke(token_id, now)

        refresh_tokens = RevokedInBetween()
        family = new_token_family()
        await store(refresh_tokens, refresh_token_generator, user, family_id=family)
        await store(refresh_tokens, refresh_token_generator, user, token="next", family_id=family)
        other_session = await store(refresh_tokens, refresh_token_generator, user, token="other")
        refresh = RefreshTokens(
            refresh_tokens, refresh_token_generator, users, token_pair_issuer, uow
        )

        with pytest.raises(InvalidRefreshTokenError):
            await refresh(TOKEN)
        assert [t.id for t in refresh_tokens.active_for(user.id)] == [other_session]
