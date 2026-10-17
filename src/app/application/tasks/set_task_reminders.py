import logging

from app.application.common.clock import utc_now
from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.notification_repository import AbstractNotificationRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.task_repository import AbstractTaskRepository
from app.application.tasks.dto import SetTaskRemindersDTO, TaskDTO
from app.application.tasks.lookup import task_of_member, task_view
from app.application.tasks.read_model import AbstractTaskReadModel
from app.application.tasks.services import TaskNotifier
from app.domain.common.reminders import ReminderOffset

logger = logging.getLogger(__name__)


class SetTaskReminders:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        tasks: AbstractTaskRepository,
        notifications: AbstractNotificationRepository,
        notifier: TaskNotifier,
        read_model: AbstractTaskReadModel,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._tasks = tasks
        self._notifications = notifications
        self._notifier = notifier
        self._read_model = read_model
        self._uow = uow

    async def __call__(self, data: SetTaskRemindersDTO) -> TaskDTO:
        """Replace the reminders (hours before the due time) as a whole.

        Removed offsets cancel their pending notifications; added ones are queued
        for every assignee if their moment is still ahead. The same set again
        changes and writes nothing.

        Raises:
            InvalidReminderOffsetError: an offset is out of 1..8760 hours.
            TaskNotFoundError: no such active task in a project of the caller.
            ReminderWithoutDueDateError: the task has no due time.
        """
        offsets = {ReminderOffset(hours) for hours in data.offsets_hours}
        task, _ = await task_of_member(
            self._tasks, self._projects, data.task_id, data.user_id, for_update=True
        )
        added = offsets - task.reminders
        removed = task.reminders - offsets

        if added or removed:
            for offset in removed:
                task.remove_reminder(offset)
            for offset in added:
                task.add_reminder(offset)
            await self._tasks.update(task)
            if removed:
                await self._notifications.cancel_pending_task_reminders(task.id, offsets=removed)
            await self._notifier.schedule_reminders(
                task, assignees=task.assignee_ids, offsets=added, now=utc_now()
            )

        view = await task_view(self._read_model, task.id)
        await self._uow.commit()
        if added or removed:
            logger.info(
                "task_reminders_changed",
                extra={
                    "task_id": str(task.id),
                    "offsets_hours": sorted(offset.hours for offset in task.reminders),
                },
            )
        return view
