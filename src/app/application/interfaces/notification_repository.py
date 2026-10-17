from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import datetime

from app.domain.common.ids import ProjectId, UserId
from app.domain.common.reminders import MovingReminderOffset
from app.domain.notifications.notification import Notification


class AbstractNotificationRepository(ABC):
    """The outbox of push notifications, written in the transaction of the change
    that causes them (docs/db-design.md, 10.2). Implementations must not commit.

    The cancel_* methods are atomic bulk versions of Notification.cancel(): they
    touch pending notifications only, so a notification being sent right now is
    either cancelled before the worker locks it or left as the worker finishes it.
    """

    @abstractmethod
    async def add(self, notification: Notification) -> None:
        """Stage a new notification."""

    @abstractmethod
    async def claim_next_due(self, now: datetime) -> Notification | None:
        """The pending notification due longest ago (scheduled_at <= now), locked
        until the transaction ends; None if nothing is due.

        Rows locked by another worker are skipped, not waited for: several
        workers share the queue without sending anything twice.
        """

    @abstractmethod
    async def update(self, notification: Notification) -> None:
        """Stage changes of an existing notification (status, attempts...)."""

    @abstractmethod
    async def cancel_pending(self, project_id: ProjectId, *, user_id: UserId | None = None) -> int:
        """Cancel pending notifications of the project (only those of `user_id` if given).

        Returns how many were cancelled.
        """

    @abstractmethod
    async def cancel_pending_moving_reminders(
        self, project_id: ProjectId, *, offsets: Iterable[MovingReminderOffset] | None = None
    ) -> int:
        """Cancel pending moving date reminders of the project (only of `offsets` if given).

        Returns how many were cancelled.
        """
