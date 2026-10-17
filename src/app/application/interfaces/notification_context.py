"""Read model: the current facts a notification is about, read at sending time
(the text of a notification is not stored, docs/db-design.md 4.10)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from app.domain.notifications.notification import Notification


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskFacts:
    title: str
    due_at: datetime | None
    is_done: bool
    is_active: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class BoxFacts:
    number: int
    status: str
    qr_token: str
    is_active: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class NotificationContext:
    project_name: str
    project_time_zone: str
    project_is_active: bool
    recipient_is_active: bool
    """The account of the recipient is not deleted."""
    recipient_is_member: bool
    task: TaskFacts | None = None
    """Set for task notifications."""
    box: BoxFacts | None = None
    """Set for box notifications."""


class AbstractNotificationContextReader(ABC):
    @abstractmethod
    async def get(self, notification: Notification) -> NotificationContext:
        """The facts about the project, the recipient and the target, as they are now."""
