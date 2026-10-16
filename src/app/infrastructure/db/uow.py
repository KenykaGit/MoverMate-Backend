from sqlalchemy.ext.asyncio import AsyncSession

from app.application.common.uow import AbstractUnitOfWork


class SqlAlchemyUnitOfWork(AbstractUnitOfWork):
    """Commits the request-scoped session shared with the repositories."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
