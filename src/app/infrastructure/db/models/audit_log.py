from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.audit.entity import AuditAction, AuditEntityType
from app.infrastructure.db.base import Base
from app.infrastructure.db.constraints import ENUM_LENGTH, one_of


class AuditLogModel(Base):
    """Append-only journal. entity_id and actor_id have no foreign keys on purpose:
    a record outlives the object it describes."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        # A B-tree is read backwards as cheaply as forwards: newest-first needs no DESC.
        Index("ix_audit_logs_project_id_created_at", "project_id", "created_at"),
        CheckConstraint(one_of("action", AuditAction), name="action"),
        CheckConstraint(one_of("entity_type", AuditEntityType), name="entity_type"),
        # The domain derives the entity type from the action: BOX_CREATED -> BOX.
        CheckConstraint("entity_type = split_part(action, '_', 1)", name="entity_type_of_action"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    actor_id: Mapped[UUID | None]
    entity_type: Mapped[str] = mapped_column(String(ENUM_LENGTH))
    entity_id: Mapped[UUID]
    action: Mapped[str] = mapped_column(String(ENUM_LENGTH))
    details: Mapped[dict[str, str]] = mapped_column(JSONB)
    # The moment of the event, set by the domain (not a bookkeeping default).
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
