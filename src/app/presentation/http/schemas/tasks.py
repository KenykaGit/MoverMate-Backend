from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field

from app.application.tasks.dto import (
    CreateTaskDTO,
    CreateTasksFromTemplatesDTO,
    SetTaskAssigneesDTO,
    SetTaskRemindersDTO,
    TaskDTO,
    TaskTemplateDTO,
    UpdateTaskDTO,
)
from app.domain.tasks.value_objects import TaskPhase
from app.presentation.http.schemas.common import PatchRequest, RequestSchema
from app.presentation.http.schemas.inventory import UserRef

# Shape limits only (a request stays small); the rules of the values are the domain's.
MAX_ASSIGNEES = 100
MAX_REMINDERS = 20
MAX_TEMPLATES = 100

# ---------------------------------------------------------------- responses


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
            assignees=[UserRef(id=user.id, username=user.username) for user in task.assignees],
            reminder_offsets_hours=list(task.reminder_offsets_hours),
            created_at=task.created_at,
            updated_at=task.updated_at,
        )


class TaskTemplate(BaseModel):
    id: str = Field(examples=["notify-management-company"], description="Stable key")
    title: str
    description: str | None
    phase: TaskPhase

    @classmethod
    def from_dto(cls, template: TaskTemplateDTO) -> Self:
        return cls(
            id=template.id,
            title=template.title,
            description=template.description,
            phase=template.phase,
        )


# ----------------------------------------------------------------- requests


class CreateTaskRequest(RequestSchema):
    title: str
    description: str | None = None
    phase: TaskPhase = TaskPhase.BEFORE
    due_at: AwareDatetime | None = None
    assignee_ids: list[UUID] = Field(default_factory=list, max_length=MAX_ASSIGNEES)
    reminder_offsets_hours: list[int] = Field(
        default_factory=list,
        max_length=MAX_REMINDERS,
        description="Hours before due_at, 1..8760; needs due_at",
    )

    def to_dto(self, *, user_id: UUID, project_id: UUID) -> CreateTaskDTO:
        return CreateTaskDTO(
            user_id=user_id,
            project_id=project_id,
            title=self.title,
            description=self.description,
            phase=self.phase,
            due_at=self.due_at,
            assignee_ids=tuple(self.assignee_ids),
            reminder_offsets_hours=tuple(self.reminder_offsets_hours),
        )


class CreateTasksFromTemplatesRequest(RequestSchema):
    template_ids: list[str] = Field(min_length=1, max_length=MAX_TEMPLATES)

    def to_dto(self, *, user_id: UUID, project_id: UUID) -> CreateTasksFromTemplatesDTO:
        return CreateTasksFromTemplatesDTO(
            user_id=user_id, project_id=project_id, template_ids=tuple(self.template_ids)
        )


class UpdateTaskRequest(PatchRequest):
    """Partial update: absent fields stay; null clears `description` and `due_at`
    (and with it the reminders) and is rejected for `title` and `phase`."""

    title: str | None = None
    description: str | None = None
    phase: TaskPhase | None = None
    due_at: AwareDatetime | None = None

    def to_dto(self, *, user_id: UUID, task_id: UUID) -> UpdateTaskDTO:
        return UpdateTaskDTO(
            user_id=user_id,
            task_id=task_id,
            title=self.sent_not_null("title", self.title),
            description=self.sent("description", self.description),
            phase=self.sent_not_null("phase", self.phase),
            due_at=self.sent("due_at", self.due_at),
        )


class SetTaskAssigneesRequest(RequestSchema):
    user_ids: list[UUID] = Field(max_length=MAX_ASSIGNEES, description="The whole new list")

    def to_dto(self, *, user_id: UUID, task_id: UUID) -> SetTaskAssigneesDTO:
        return SetTaskAssigneesDTO(
            user_id=user_id, task_id=task_id, assignee_ids=tuple(self.user_ids)
        )


class SetTaskRemindersRequest(RequestSchema):
    offsets_hours: list[int] = Field(
        max_length=MAX_REMINDERS, description="The whole new list: hours before due_at, 1..8760"
    )

    def to_dto(self, *, user_id: UUID, task_id: UUID) -> SetTaskRemindersDTO:
        return SetTaskRemindersDTO(
            user_id=user_id, task_id=task_id, offsets_hours=tuple(self.offsets_hours)
        )
