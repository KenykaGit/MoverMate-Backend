from abc import ABC, abstractmethod


class AbstractUnitOfWork(ABC):
    """The transaction shared by all repositories within one request.

    Repositories only stage changes; nothing is persisted until a use case
    calls commit(). Application services never commit: the use case that
    calls them owns the transaction boundary.
    """

    @abstractmethod
    async def commit(self) -> None:
        """Persist all changes staged by repositories."""

    @abstractmethod
    async def rollback(self) -> None:
        """Discard all changes staged by repositories."""
