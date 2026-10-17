from app.domain.common.errors import DomainError


class InvalidTaskTitleError(DomainError):
    """The task title does not satisfy domain rules."""


class InvalidTaskDescriptionError(DomainError):
    """The task description does not satisfy domain rules."""


class InvalidDueDateError(DomainError):
    """A due time must be an absolute moment (timezone-aware)."""


class ReminderWithoutDueDateError(DomainError):
    """Reminders are relative to the due time, so a task needs one first."""
