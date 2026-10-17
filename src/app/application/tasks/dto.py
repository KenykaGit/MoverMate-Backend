from dataclasses import dataclass, field
from datetime import datetime
from typing import Self
from uuid import UUID

from app.application.common.patch import UNSET, Patch
from app.application.interfaces.project_reader import UserRefDTO
from app.domain.tasks.value_objects import TaskPhase, TaskTemplate

# ------------------------------------------------------------------ outputs


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskDTO:
    id: UUID
    title: str
    description: str | None
    phase: TaskPhase
    due_at: datetime | None
    is_done: bool
    done_at: datetime | None
    assignees: tuple[UserRefDTO, ...]
    """Assignees with an active account, by username."""
    reminder_offsets_hours: tuple[int, ...]
    """Ascending."""
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskTemplateDTO:
    id: str
    title: str
    description: str | None
    phase: TaskPhase

    @classmethod
    def from_entity(cls, template: TaskTemplate) -> Self:
        return cls(
            id=template.id,
            title=template.title.value,
            description=None if template.description is None else template.description.value,
            phase=template.phase,
        )


# ------------------------------------------------------------------- inputs


@dataclass(frozen=True, slots=True, kw_only=True)
class ListTasksDTO:
    user_id: UUID
    project_id: UUID
    phase: TaskPhase | None = None
    is_done: bool | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateTaskDTO:
    user_id: UUID
    project_id: UUID
    title: str
    description: str | None = None
    phase: TaskPhase = TaskPhase.BEFORE
    due_at: datetime | None = None
    assignee_ids: tuple[UUID, ...] = ()
    reminder_offsets_hours: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateTasksFromTemplatesDTO:
    user_id: UUID
    project_id: UUID
    template_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskActionDTO:
    """`user_id` gets, completes, reopens or deletes a task."""

    user_id: UUID
    task_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdateTaskDTO:
    user_id: UUID
    task_id: UUID
    title: Patch[str] = UNSET
    description: Patch[str | None] = UNSET
    phase: Patch[TaskPhase] = UNSET
    due_at: Patch[datetime | None] = UNSET


@dataclass(frozen=True, slots=True, kw_only=True)
class SetTaskAssigneesDTO:
    user_id: UUID
    task_id: UUID
    assignee_ids: tuple[UUID, ...] = field(default=())


@dataclass(frozen=True, slots=True, kw_only=True)
class SetTaskRemindersDTO:
    user_id: UUID
    task_id: UUID
    offsets_hours: tuple[int, ...] = field(default=())
