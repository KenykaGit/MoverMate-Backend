"""Task value objects: immutable, self-validating, normalized on creation."""

from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True)
class TaskTemplate:
    """A task every new project starts with. No due time and no assignees by design."""

    title: TaskTitle
    description: TaskDescription | None = None
