from abc import ABC, abstractmethod

from app.domain.audit.entity import AuditEntry


class AbstractAuditLogRepository(ABC):
    """The append-only journal of a project. Implementations must not commit:
    an entry is written in the transaction of the change it describes."""

    @abstractmethod
    async def add(self, entry: AuditEntry) -> None:
        """Stage a new entry."""
