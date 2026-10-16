from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import ColumnElement, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.exceptions import (
    EmailAlreadyTakenError,
    UsernameAlreadyTakenError,
    UserNotFoundError,
)
from app.application.interfaces.user_repository import AbstractUserRepository
from app.domain.common.ids import UserId
from app.domain.users.entity import User
from app.domain.users.value_objects import Email, Username
from app.infrastructure.db.errors import violated_constraint
from app.infrastructure.db.mappers.user import UserMapper
from app.infrastructure.db.models.user import (
    ACTIVE_EMAIL_UNIQUE_INDEX,
    ACTIVE_USERNAME_UNIQUE_INDEX,
    UserModel,
)


class SqlAlchemyUserRepository(AbstractUserRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, user: User) -> None:
        async with self._flush_or_raise_conflict(user):
            self._session.add(UserMapper.to_model(user))

    async def get_by_id(self, user_id: UserId, *, include_deleted: bool = False) -> User | None:
        # get() rather than a query: it can skip the database via the identity map.
        model = await self._session.get(UserModel, user_id)
        if model is None or not (model.is_active or include_deleted):
            return None
        return UserMapper.to_domain(model)

    async def get_by_email(self, email: Email) -> User | None:
        return await self._get_active(UserModel.email == email.value)

    async def get_by_username(self, username: Username) -> User | None:
        return await self._get_active(UserModel.username == username.value)

    async def update(self, user: User) -> None:
        # merge() alone would insert a missing user, so check existence first.
        # Holding `existing` keeps it in the (weak) identity map, so merge()
        # reuses it instead of issuing a second SELECT.
        existing = await self._session.get(UserModel, user.id)
        if existing is None:
            raise UserNotFoundError(user.id)
        async with self._flush_or_raise_conflict(user):
            await self._session.merge(UserMapper.to_model(user))

    async def _get_active(self, condition: ColumnElement[bool]) -> User | None:
        model = await self._session.scalar(select(UserModel).where(condition, UserModel.is_active))
        return None if model is None else UserMapper.to_domain(model)

    @asynccontextmanager
    async def _flush_or_raise_conflict(self, user: User) -> AsyncIterator[None]:
        """Flush right away and turn unique violations into application errors.

        Flushing here, not at commit, lets the error name the taken value. The
        SAVEPOINT confines the failure to this change: the rest of the
        transaction stays intact and the session stays usable.
        """
        try:
            async with self._session.begin_nested():
                yield
        except IntegrityError as error:
            constraint = violated_constraint(error)
            if constraint == ACTIVE_EMAIL_UNIQUE_INDEX:
                raise EmailAlreadyTakenError(user.email) from error
            if constraint == ACTIVE_USERNAME_UNIQUE_INDEX:
                raise UsernameAlreadyTakenError(user.username) from error
            raise
