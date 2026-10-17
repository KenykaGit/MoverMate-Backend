"""Task value objects: immutable, self-validating, normalized on creation."""

from dataclasses import dataclass
from enum import StrEnum
from typing import NewType

from app.domain.common.text import bounded_text
from app.domain.tasks.errors import InvalidTaskDescriptionError, InvalidTaskTitleError

TASK_TITLE_MAX_LENGTH = 200
TASK_DESCRIPTION_MAX_LENGTH = 5000


@dataclass(frozen=True, slots=True)
class TaskTitle:
    value: str

    def __post_init__(self) -> None:
        normalized = bounded_text(
            self.value,
            max_length=TASK_TITLE_MAX_LENGTH,
            what="Task title",
            error=InvalidTaskTitleError,
        )
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class TaskDescription:
    value: str

    def __post_init__(self) -> None:
        normalized = bounded_text(
            self.value,
            max_length=TASK_DESCRIPTION_MAX_LENGTH,
            what="Task description",
            error=InvalidTaskDescriptionError,
        )
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


class TaskPhase(StrEnum):
    """When in the move a task belongs: the plan is shown in these three columns."""

    BEFORE = "BEFORE"
    DURING = "DURING"
    """The moving day itself."""
    AFTER = "AFTER"


TaskTemplateId = NewType("TaskTemplateId", str)
"""Stable key of a template in the templates file: clients select templates by it."""


@dataclass(frozen=True, slots=True)
class TaskTemplate:
    """A ready-made task a user may add to their project. No due time and no assignees
    by design; the copy keeps no link to the template."""

    id: TaskTemplateId
    title: TaskTitle
    phase: TaskPhase
    description: TaskDescription | None = None
