from app.domain.audit.entity import AuditAction, AuditEntry, AuditEntryId
from app.domain.common.ids import ProjectId, UserId
from app.infrastructure.db.models.audit_log import AuditLogModel


class AuditEntryMapper:
    """entity_type is stored for querying; the domain derives it from the action."""

    @staticmethod
    def to_domain(model: AuditLogModel) -> AuditEntry:
        return AuditEntry(
            id=AuditEntryId(model.id),
            project_id=ProjectId(model.project_id),
            actor_id=None if model.actor_id is None else UserId(model.actor_id),
            action=AuditAction(model.action),
            entity_id=model.entity_id,
            details=dict(model.details),
            created_at=model.created_at,
        )

    @staticmethod
    def to_model(entry: AuditEntry) -> AuditLogModel:
        return AuditLogModel(
            id=entry.id,
            project_id=entry.project_id,
            actor_id=entry.actor_id,
            entity_type=entry.entity_type.value,
            entity_id=entry.entity_id,
            action=entry.action.value,
            details=dict(entry.details),
            created_at=entry.created_at,
        )
