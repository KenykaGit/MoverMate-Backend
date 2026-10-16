import asyncio

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.exceptions import (
    EmailAlreadyTakenError,
    UsernameAlreadyTakenError,
    UserNotFoundError,
)
from app.domain.common.ids import UserId
from app.domain.users.entity import User
from app.domain.users.value_objects import Email, PasswordHash, Username
from app.infrastructure.db.repositories.user import SqlAlchemyUserRepository
from tests.factories import make_user, user_state

type SessionMaker = async_sessionmaker[AsyncSession]


async def save(session_maker: SessionMaker, *users: User) -> None:
    async with session_maker() as session:
        repository = SqlAlchemyUserRepository(session)
        for user in users:
            await repository.add(user)
        await session.commit()


async def load(session_maker: SessionMaker, user_id: UserId) -> User | None:
    """What is actually in the database: read in a fresh session."""
    async with session_maker() as session:
        return await SqlAlchemyUserRepository(session).get_by_id(user_id, include_deleted=True)


class TestAdd:
    @pytest.mark.parametrize("is_active", [True, False])
    async def test_persists_all_attributes(
        self, session_maker: SessionMaker, is_active: bool
    ) -> None:
        user = make_user(is_active=is_active)

        await save(session_maker, user)

        loaded = await load(session_maker, user.id)
        assert loaded is not None
        assert user_state(loaded) == user_state(user)

    async def test_is_not_persisted_without_commit(self, session_maker: SessionMaker) -> None:
        user = make_user()
        async with session_maker() as session:
            await SqlAlchemyUserRepository(session).add(user)

        assert await load(session_maker, user.id) is None

    @pytest.mark.parametrize(
        ("email", "username", "error"),
        [
            ("JOHN@example.com", "other", EmailAlreadyTakenError),
            ("other@example.com", "John_Doe", UsernameAlreadyTakenError),
        ],
    )
    async def test_rejects_values_taken_by_an_active_user(
        self,
        session_maker: SessionMaker,
        session: AsyncSession,
        email: str,
        username: str,
        error: type[Exception],
    ) -> None:
        await save(session_maker, make_user())

        with pytest.raises(error):
            await SqlAlchemyUserRepository(session).add(make_user(email=email, username=username))

    async def test_conflict_keeps_the_rest_of_the_transaction(
        self, session_maker: SessionMaker
    ) -> None:
        """The SAVEPOINT confines the failure: the session stays usable."""
        await save(session_maker, make_user())
        jane = make_user(email="jane@example.com", username="jane")
        duplicate = make_user(username="other")

        async with session_maker() as session:
            repository = SqlAlchemyUserRepository(session)
            await repository.add(jane)
            with pytest.raises(EmailAlreadyTakenError):
                await repository.add(duplicate)
            await session.commit()

        assert await load(session_maker, jane.id) == jane
        assert await load(session_maker, duplicate.id) is None

    async def test_rejects_duplicate_from_a_concurrent_transaction(
        self, session_maker: SessionMaker
    ) -> None:
        """Both transactions passed a get_by_email() check, then race to insert."""
        john = make_user()
        rival = make_user(username="rival")

        async with session_maker() as first, session_maker() as second:
            await SqlAlchemyUserRepository(first).add(john)
            # Blocks on the unique index until the first transaction ends.
            rival_insert = asyncio.create_task(SqlAlchemyUserRepository(second).add(rival))
            await asyncio.sleep(0.1)
            await first.commit()

            with pytest.raises(EmailAlreadyTakenError):
                await rival_insert

        assert await load(session_maker, john.id) == john
        assert await load(session_maker, rival.id) is None

    async def test_other_integrity_errors_are_not_disguised(
        self, session_maker: SessionMaker
    ) -> None:
        user = make_user()
        await save(session_maker, user)
        same_id = User(
            id=user.id,
            email=Email("jane@example.com"),
            username=Username("jane"),
            password_hash=PasswordHash("hash"),
            is_active=True,
        )

        with pytest.raises(IntegrityError):
            await save(session_maker, same_id)


class TestSoftDelete:
    @pytest.fixture
    async def deleted(self, session_maker: SessionMaker) -> User:
        user = make_user()
        await save(session_maker, user)
        async with session_maker() as session:
            user.soft_delete()
            await SqlAlchemyUserRepository(session).update(user)
            await session.commit()
        return user

    async def test_keeps_the_row_but_hides_it_from_lookups(
        self, session_maker: SessionMaker, session: AsyncSession, deleted: User
    ) -> None:
        repository = SqlAlchemyUserRepository(session)

        assert await repository.get_by_id(deleted.id) is None
        assert await repository.get_by_email(deleted.email) is None
        assert await repository.get_by_username(deleted.username) is None
        stored = await load(session_maker, deleted.id)
        assert stored is not None
        assert user_state(stored) == user_state(deleted)

    @pytest.mark.usefixtures("deleted")
    async def test_frees_email_and_username_at_once(self, session_maker: SessionMaker) -> None:
        newcomer = make_user()

        await save(session_maker, newcomer)

        async with session_maker() as session:
            assert await SqlAlchemyUserRepository(session).get_by_email(newcomer.email) == newcomer

    async def test_several_deleted_users_may_share_values(
        self, session_maker: SessionMaker, deleted: User
    ) -> None:
        another = make_user()
        await save(session_maker, another)
        async with session_maker() as session:
            another.soft_delete()
            await SqlAlchemyUserRepository(session).update(another)
            await session.commit()

        assert await load(session_maker, deleted.id) is not None
        assert await load(session_maker, another.id) is not None

    async def test_restore_fails_if_the_email_was_taken_meanwhile(
        self, session_maker: SessionMaker, deleted: User
    ) -> None:
        await save(session_maker, make_user(username="newcomer"))

        async with session_maker() as session:
            deleted.restore()
            with pytest.raises(EmailAlreadyTakenError):
                await SqlAlchemyUserRepository(session).update(deleted)
            await session.commit()

        stored = await load(session_maker, deleted.id)
        assert stored is not None
        assert stored.is_active is False


class TestUpdate:
    async def test_persists_all_attributes(self, session_maker: SessionMaker) -> None:
        user = make_user()
        await save(session_maker, user)
        changed = User(
            id=user.id,
            email=Email("new@example.com"),
            username=Username("new_name"),
            password_hash=PasswordHash("new-hash"),
            is_active=True,
        )

        async with session_maker() as session:
            await SqlAlchemyUserRepository(session).update(changed)
            await session.commit()

        reloaded = await load(session_maker, user.id)
        assert reloaded is not None
        assert user_state(reloaded) == user_state(changed)

    async def test_rejects_a_missing_user_instead_of_inserting_it(
        self, session_maker: SessionMaker
    ) -> None:
        user = make_user()

        async with session_maker() as session:
            with pytest.raises(UserNotFoundError):
                await SqlAlchemyUserRepository(session).update(user)
            await session.commit()

        assert await load(session_maker, user.id) is None
