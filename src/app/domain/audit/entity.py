from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import NewType, Self
from uuid import UUID

from app.domain.common.entity import Entity
from app.domain.common.ids import ProjectId, UserId, new_id

AuditEntryId = NewType("AuditEntryId", UUID)


class AuditEntityType(StrEnum):
    PROJECT = "PROJECT"
    BOX = "BOX"
    TASK = "TASK"
    ITEM = "ITEM"


class AuditAction(StrEnum):
    PROJECT_CREATED = "PROJECT_CREATED"
    BOX_CREATED = "BOX_CREATED"
    BOX_STATUS_CHANGED = "BOX_STATUS_CHANGED"
    BOX_STATUS_ROLLED_BACK = "BOX_STATUS_ROLLED_BACK"
    BOX_DELETED = "BOX_DELETED"
    TASK_CREATED = "TASK_CREATED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_REOPENED = "TASK_REOPENED"
    ITEM_MARKED_LOST = "ITEM_MARKED_LOST"
    ITEM_RETURNED = "ITEM_RETURNED"

    @property
    def entity_type(self) -> AuditEntityType:
        """Every action is about one kind of object, so the pair cannot disagree."""
        return AuditEntityType(self.split("_", maxsplit=1)[0])


@dataclass(eq=False, kw_only=True, slots=True)
class AuditEntry(Entity[AuditEntryId]):
    """An immutable record of an important event in a project.

    Refers to its object by id only, without a foreign key: the record
    outlives the object.
    """

    project_id: ProjectId
    actor_id: UserId | None
    """Who did it; None for the system (e.g. the notification worker)."""
    action: AuditAction
    entity_id: UUID
    details: Mapping[str, str]
    """E.g. {"from": "PACKED", "to": "LOADED"}."""
    created_at: datetime

    @classmethod
    def record(
        cls,
        *,
        project_id: ProjectId,
        actor_id: UserId | None,
        action: AuditAction,
        entity_id: UUID,
        now: datetime,
        details: Mapping[str, str] | None = None,
    ) -> Self:
        return cls(
            id=AuditEntryId(new_id()),
            project_id=project_id,
            actor_id=actor_id,
            action=action,
            entity_id=entity_id,
            details=dict(details or {}),
            created_at=now,
        )

    @property
    def entity_type(self) -> AuditEntityType:
        return self.action.entity_type
