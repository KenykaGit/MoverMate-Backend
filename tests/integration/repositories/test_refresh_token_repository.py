import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.tokens.entity import new_token_family
from app.domain.users.entity import User
from app.infrastructure.db.repositories.refresh_token import SqlAlchemyRefreshTokenRepository
from app.infrastructure.db.repositories.user import SqlAlchemyUserRepository
from tests.factories import make_refresh_token, make_user

type SessionMaker = async_sessionmaker[AsyncSession]


async def saved_user(session_maker: SessionMaker) -> User:
    user = make_user()
    async with session_maker() as session:
        await SqlAlchemyUserRepository(session).add(user)
        await session.commit()
    return user


async def test_roundtrip_by_hash(session_maker: SessionMaker) -> None:
    user = await saved_user(session_maker)
    token = make_refresh_token(user.id)

    async with session_maker() as session:
        await SqlAlchemyRefreshTokenRepository(session).add(token)
        await session.commit()
    async with session_maker() as session:
        loaded = await SqlAlchemyRefreshTokenRepository(session).get_by_hash(token.token_hash)

    assert loaded == token
    assert loaded is not None
    assert (loaded.user_id, loaded.family_id, loaded.expires_at, loaded.revoked_at) == (
        user.id,
        token.family_id,
        token.expires_at,
        None,
    )


async def test_concurrent_revocations_succeed_exactly_once(session_maker: SessionMaker) -> None:
    """UPDATE ... WHERE revoked_at IS NULL RETURNING id: one winner, even in a race."""
    user = await saved_user(session_maker)
    token = make_refresh_token(user.id)
    async with session_maker() as session:
        await SqlAlchemyRefreshTokenRepository(session).add(token)
        await session.commit()

    async def revoke() -> bool:
        async with session_maker() as session:
            revoked = await SqlAlchemyRefreshTokenRepository(session).revoke(
                token.id, datetime.now(UTC)
            )
            await asyncio.sleep(0.05)  # hold the row lock while the rival waits
            await session.commit()
            return revoked

    results = await asyncio.gather(revoke(), revoke(), revoke())

    assert sorted(results) == [False, False, True]


async def test_revoke_all_for_user(session_maker: SessionMaker) -> None:
    user = await saved_user(session_maker)
    other = make_user(username="other")
    async with session_maker() as session:
        await SqlAlchemyUserRepository(session).add(other)
        tokens = SqlAlchemyRefreshTokenRepository(session)
        mine = [make_refresh_token(user.id), make_refresh_token(user.id)]
        theirs = make_refresh_token(other.id)
        for token in [*mine, theirs]:
            await tokens.add(token)
        await session.commit()

    async with session_maker() as session:
        await SqlAlchemyRefreshTokenRepository(session).revoke_all_for_user(
            user.id, datetime.now(UTC)
        )
        await session.commit()

    async with session_maker() as session:
        tokens = SqlAlchemyRefreshTokenRepository(session)
        for token in mine:
            loaded = await tokens.get_by_hash(token.token_hash)
            assert loaded is not None
            assert loaded.is_revoked
        loaded_theirs = await tokens.get_by_hash(theirs.token_hash)
        assert loaded_theirs is not None
        assert not loaded_theirs.is_revoked


async def test_revoke_family(session_maker: SessionMaker) -> None:
    user = await saved_user(session_maker)
    family = new_token_family()
    rotated = make_refresh_token(user.id, family_id=family, revoked=True)
    current = make_refresh_token(user.id, family_id=family)
    other_session = make_refresh_token(user.id)
    async with session_maker() as session:
        tokens = SqlAlchemyRefreshTokenRepository(session)
        for token in (rotated, current, other_session):
            await tokens.add(token)
        await session.commit()

    async with session_maker() as session:
        tokens = SqlAlchemyRefreshTokenRepository(session)
        revoked = await tokens.revoke_family(family, datetime.now(UTC))
        again = await tokens.revoke_family(family, datetime.now(UTC))
        await session.commit()

    async with session_maker() as session:
        tokens = SqlAlchemyRefreshTokenRepository(session)
        loaded_current = await tokens.get_by_hash(current.token_hash)
        loaded_other = await tokens.get_by_hash(other_session.token_hash)

    assert (revoked, again) == (1, 0), "already revoked tokens are not counted"
    assert loaded_current is not None
    assert loaded_current.is_revoked
    assert loaded_other is not None
    assert not loaded_other.is_revoked
