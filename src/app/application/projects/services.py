from collections.abc import Iterable
from datetime import datetime

from app.application.interfaces.notification_repository import AbstractNotificationRepository
from app.domain.common.ids import UserId
from app.domain.common.reminders import MovingReminderOffset
from app.domain.notifications.notification import MovingDateReminder, Notification
from app.domain.projects.entity import Project


class MovingReminderScheduler:
    """Keeps pending moving date reminders in line with the project
    (docs/db-design.md, 10.2). Stages changes only: the calling use case commits."""

    def __init__(self, notifications: AbstractNotificationRepository) -> None:
        self._notifications = notifications

    async def schedule(
        self,
        project: Project,
        *,
        members: Iterable[UserId],
        offsets: Iterable[MovingReminderOffset],
        now: datetime,
    ) -> int:
        """Queue a reminder per member and offset; moments already past are skipped.

        Returns how many were queued.
        """
        queued = 0
        offsets = sorted(offsets)
        for user_id in sorted(members):
            for offset in offsets:
                notification = Notification.schedule(
                    project_id=project.id,
                    user_id=user_id,
                    target=MovingDateReminder(offset),
                    at=project.moving_reminder_at(offset),
                    now=now,
                )
                if notification is not None:
                    await self._notifications.add(notification)
                    queued += 1
        return queued

    async def reschedule_all(self, project: Project, *, now: datetime) -> None:
        """After the moving date or the time zone changed: every pending reminder
        is replaced by one at the new moment (or none, if that moment is past)."""
        await self._notifications.cancel_pending_moving_reminders(project.id)
        await self.schedule(
            project, members=project.member_ids, offsets=project.moving_reminders, now=now
        )
