from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field

from app.application.summaries.dto import TaskDTO
from app.domain.tasks.value_objects import TaskPhase
from app.presentation.http.schemas.inventory import UserRef


class Task(BaseModel):
    id: UUID
    title: str
    description: str | None
    phase: TaskPhase
    due_at: datetime | None
    is_done: bool
    done_at: datetime | None
    assignees: list[UserRef] = Field(description="By username")
    reminder_offsets_hours: list[int] = Field(
        description="Hours before due_at when the assignees are reminded, ascending"
    )
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_dto(cls, task: TaskDTO) -> Self:
        return cls(
            id=task.id,
            title=task.title,
            description=task.description,
            phase=task.phase,
            due_at=task.due_at,
            is_done=task.is_done,
            done_at=task.done_at,
            assignees=[UserRef.from_dto(user) for user in task.assignees],
            reminder_offsets_hours=list(task.reminder_offsets_hours),
            created_at=task.created_at,
            updated_at=task.updated_at,
        )
