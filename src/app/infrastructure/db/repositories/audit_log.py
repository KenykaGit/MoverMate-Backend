from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.audit_log_repository import AbstractAuditLogRepository
from app.domain.audit.entity import AuditEntry
from app.infrastructure.db.mappers.audit import AuditEntryMapper


class SqlAlchemyAuditLogRepository(AbstractAuditLogRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entry: AuditEntry) -> None:
        self._session.add(AuditEntryMapper.to_model(entry))
