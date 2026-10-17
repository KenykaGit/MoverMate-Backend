from collections.abc import Iterable
from datetime import datetime

from app.application.interfaces.notification_repository import AbstractNotificationRepository
from app.domain.common.ids import UserId
from app.domain.common.reminders import ReminderOffset
from app.domain.notifications.notification import Notification, TaskAssigned, TaskReminder
from app.domain.tasks.entity import Task


class TaskNotifier:
    """Keeps pending task notifications in line with the task (docs/db-design.md,
    10.1 and 10.2). Stages changes only: the calling use case commits."""

    def __init__(self, notifications: AbstractNotificationRepository) -> None:
        self._notifications = notifications

    async def assigned(
        self, task: Task, assignees: Iterable[UserId], *, by: UserId, now: datetime
    ) -> int:
        """Tell new assignees about the task, except whoever assigned themselves.

        Returns how many notifications were queued.
        """
        queued = 0
        for user_id in sorted(assignees):
            if user_id == by:
                continue
            notification = Notification.schedule(
                project_id=task.project_id,
                user_id=user_id,
                target=TaskAssigned(task.id),
                at=now,
                now=now,
            )
            if notification is not None:
                await self._notifications.add(notification)
                queued += 1
        return queued

    async def schedule_reminders(
        self,
        task: Task,
        *,
        assignees: Iterable[UserId],
        offsets: Iterable[ReminderOffset],
        now: datetime,
    ) -> int:
        """Queue a reminder per assignee and offset of the task.

        Nothing for a task that is done, deleted or has no due time; moments already
        past are skipped. Returns how many were queued.
        """
        if task.is_done or not task.is_active:
            return 0
        times = task.reminder_times()
        moments = sorted((offset, times[offset]) for offset in offsets if offset in times)
        queued = 0
        for user_id in sorted(assignees):
            for offset, at in moments:
                notification = Notification.schedule(
                    project_id=task.project_id,
                    user_id=user_id,
                    target=TaskReminder(task.id, offset),
                    at=at,
                    now=now,
                )
                if notification is not None:
                    await self._notifications.add(notification)
                    queued += 1
        return queued

    async def reschedule_reminders(self, task: Task, *, now: datetime) -> None:
        """After the due time changed or the task was reopened: every pending
        reminder is replaced by one at the new moment (or none, if it has passed)."""
        await self._notifications.cancel_pending_task_reminders(task.id)
        await self.schedule_reminders(
            task, assignees=task.assignee_ids, offsets=task.reminders, now=now
        )
