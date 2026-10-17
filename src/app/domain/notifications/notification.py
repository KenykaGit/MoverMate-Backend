from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import NewType, Self
from uuid import UUID

from app.domain.common.entity import Entity
from app.domain.common.ids import BoxId, ProjectId, TaskId, UserId, new_id
from app.domain.common.reminders import ReminderOffset
from app.domain.notifications.errors import NotificationAlreadyFinishedError

NotificationId = NewType("NotificationId", UUID)

MAX_ATTEMPTS = 5
RETRY_BASE_DELAY = timedelta(minutes=1)
LAST_ERROR_MAX_LENGTH = 1000


class NotificationType(StrEnum):
    TASK_ASSIGNED = "TASK_ASSIGNED"
    TASK_REMINDER = "TASK_REMINDER"
    BOX_CONTENTS_REMINDER = "BOX_CONTENTS_REMINDER"


@dataclass(frozen=True, slots=True)
class TaskAssigned:
    task_id: TaskId


@dataclass(frozen=True, slots=True)
class TaskReminder:
    task_id: TaskId
    offset: ReminderOffset


@dataclass(frozen=True, slots=True)
class BoxContentsReminder:
    box_id: BoxId


type NotificationTarget = TaskAssigned | TaskReminder | BoxContentsReminder
"""What a notification is about. One type per target, so the database rule
"the type decides which of task_id, box_id, offset are set" holds by construction."""

_TYPES: dict[type[NotificationTarget], NotificationType] = {
    TaskAssigned: NotificationType.TASK_ASSIGNED,
    TaskReminder: NotificationType.TASK_REMINDER,
    BoxContentsReminder: NotificationType.BOX_CONTENTS_REMINDER,
}


class NotificationStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    """Gave up after MAX_ATTEMPTS."""
    CANCELLED = "CANCELLED"
    """Its reason went away: the task was done, the box packed, the member left..."""
    SKIPPED = "SKIPPED"
    """The user had no active push subscription."""


_P = NotificationStatus.PENDING
NOTIFICATION_TRANSITIONS: frozenset[tuple[NotificationStatus, NotificationStatus]] = frozenset(
    (_P, status) for status in NotificationStatus if status is not _P
)
"""Allowed (from, to) pairs: only a pending notification changes, and once.

The database keeps a copy of this matrix; a test checks that both agree.
"""


@dataclass(eq=False, kw_only=True, slots=True)
class Notification(Entity[NotificationId]):
    """One push notification to one user: a row of the outbox the worker sends from.

    Created in the same transaction as the change that causes it, so none is
    lost. The text is not stored: it is built from current data when sent.
    """

    project_id: ProjectId
    user_id: UserId
    target: NotificationTarget
    scheduled_at: datetime
    status: NotificationStatus
    attempts: int
    last_error: str | None
    sent_at: datetime | None

    @classmethod
    def schedule(
        cls,
        *,
        project_id: ProjectId,
        user_id: UserId,
        target: NotificationTarget,
        at: datetime,
        now: datetime,
    ) -> Self | None:
        """A pending notification to send at `at`; None if that moment has passed."""
        if at < now:
            return None
        return cls(
            id=NotificationId(new_id()),
            project_id=project_id,
            user_id=user_id,
            target=target,
            scheduled_at=at,
            status=NotificationStatus.PENDING,
            attempts=0,
            last_error=None,
            sent_at=None,
        )

    @property
    def type(self) -> NotificationType:
        return _TYPES[type(self.target)]

    @property
    def is_pending(self) -> bool:
        return self.status is NotificationStatus.PENDING

    def mark_sent(self, *, now: datetime) -> None:
        self._finish(NotificationStatus.SENT)
        self.sent_at = now

    def mark_skipped(self) -> None:
        """Nowhere to send: the user has no active push subscription."""
        self._finish(NotificationStatus.SKIPPED)

    def record_failure(self, error: str, *, now: datetime) -> None:
        """Retry later with an exponential delay, or give up after MAX_ATTEMPTS."""
        self._ensure_pending()
        self.attempts += 1
        self.last_error = error[:LAST_ERROR_MAX_LENGTH]
        if self.attempts >= MAX_ATTEMPTS:
            self.status = NotificationStatus.FAILED
        else:
            self.scheduled_at = now + RETRY_BASE_DELAY * 2 ** (self.attempts - 1)

    def cancel(self) -> bool:
        """Cancel if still pending; False if it was already sent or finished otherwise."""
        if not self.is_pending:
            return False
        self.status = NotificationStatus.CANCELLED
        return True

    def _finish(self, status: NotificationStatus) -> None:
        self._ensure_pending()
        self.status = status

    def _ensure_pending(self) -> None:
        if not self.is_pending:
            raise NotificationAlreadyFinishedError(
                f"Notification {self.id} is already {self.status}"
            )
