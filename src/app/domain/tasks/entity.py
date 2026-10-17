from dataclasses import dataclass
from datetime import datetime
from typing import Self

from app.domain.common.entity import Entity
from app.domain.common.ids import ProjectId, TaskId, UserId, new_id
from app.domain.common.reminders import ReminderOffset
from app.domain.tasks.errors import InvalidDueDateError, ReminderWithoutDueDateError
from app.domain.tasks.value_objects import TaskDescription, TaskPhase, TaskTemplate, TaskTitle


@dataclass(eq=False, kw_only=True, slots=True)
class Task(Entity[TaskId]):
    """A to-do of a project, optionally due at a moment and assigned to members.

    Assignees and reminders belong to the aggregate. That assignees are members
    of the project is a rule of another aggregate: the use case checks it
    before `assign`, the database guards it with a composite foreign key.
    """

    project_id: ProjectId
    title: TaskTitle
    description: TaskDescription | None
    phase: TaskPhase
    due_at: datetime | None
    done_at: datetime | None
    """Set exactly when the task is done."""
    assignee_ids: set[UserId]
    reminders: set[ReminderOffset]
    """Empty whenever due_at is None."""
    is_active: bool

    @classmethod
    def create(
        cls,
        *,
        project_id: ProjectId,
        title: TaskTitle,
        description: TaskDescription | None = None,
        phase: TaskPhase = TaskPhase.BEFORE,
        due_at: datetime | None = None,
    ) -> Self:
        _check_due_at(due_at)
        return cls(
            id=TaskId(new_id()),
            project_id=project_id,
            title=title,
            description=description,
            phase=phase,
            due_at=due_at,
            done_at=None,
            assignee_ids=set(),
            reminders=set(),
            is_active=True,
        )

    @classmethod
    def from_template(cls, template: TaskTemplate, *, project_id: ProjectId) -> Self:
        """A copy of `template`; it keeps no link to the template, so duplicates are allowed."""
        return cls.create(
            project_id=project_id,
            title=template.title,
            description=template.description,
            phase=template.phase,
        )

    @property
    def is_done(self) -> bool:
        return self.done_at is not None

    def complete(self, *, now: datetime) -> bool:
        """Mark done; False if it already was (the first completion time is kept)."""
        if self.done_at is not None:
            return False
        self.done_at = now
        return True

    def reopen(self) -> bool:
        """Mark not done; False if it was not done."""
        if self.done_at is None:
            return False
        self.done_at = None
        return True

    def reschedule(self, due_at: datetime | None) -> None:
        """Change or clear the due time. Clearing it drops the reminders too."""
        _check_due_at(due_at)
        self.due_at = due_at
        if due_at is None:
            self.reminders.clear()

    def add_reminder(self, offset: ReminderOffset) -> bool:
        """False if the task already had this reminder."""
        if self.due_at is None:
            raise ReminderWithoutDueDateError("Set a due time before adding reminders")
        if offset in self.reminders:
            return False
        self.reminders.add(offset)
        return True

    def remove_reminder(self, offset: ReminderOffset) -> bool:
        """False if the task had no such reminder."""
        if offset not in self.reminders:
            return False
        self.reminders.remove(offset)
        return True

    def reminder_times(self) -> dict[ReminderOffset, datetime]:
        """When each reminder is due; empty without a due time."""
        if self.due_at is None:
            return {}
        return {offset: self.due_at - offset.delta for offset in self.reminders}

    def assign(self, user_id: UserId) -> bool:
        """False if `user_id` already was an assignee."""
        if user_id in self.assignee_ids:
            return False
        self.assignee_ids.add(user_id)
        return True

    def unassign(self, user_id: UserId) -> bool:
        """False if `user_id` was not an assignee."""
        if user_id not in self.assignee_ids:
            return False
        self.assignee_ids.remove(user_id)
        return True

    def rename(self, title: TaskTitle) -> None:
        self.title = title

    def move_to_phase(self, phase: TaskPhase) -> None:
        self.phase = phase

    def change_description(self, description: TaskDescription | None) -> None:
        self.description = description

    def soft_delete(self) -> None:
        """Idempotent."""
        self.is_active = False


def _check_due_at(due_at: datetime | None) -> None:
    # A naive time is ambiguous: users see due times in their own time zones.
    if due_at is not None and due_at.utcoffset() is None:
        raise InvalidDueDateError("Due time must include a time zone")
