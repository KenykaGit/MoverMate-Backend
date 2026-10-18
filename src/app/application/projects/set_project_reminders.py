import logging

from app.application.common.clock import utc_now
from app.application.common.membership import project_of_member
from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.notification_repository import AbstractNotificationRepository
from app.application.interfaces.project_reader import AbstractProjectReader, ProjectDTO
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.projects.dto import SetProjectRemindersDTO
from app.application.projects.lookup import read_project_or_raise
from app.application.projects.services import MovingReminderScheduler
from app.domain.common.reminders import MovingReminderOffset

logger = logging.getLogger(__name__)


class SetProjectReminders:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        notifications: AbstractNotificationRepository,
        reminders: MovingReminderScheduler,
        reader: AbstractProjectReader,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._notifications = notifications
        self._reminders = reminders
        self._reader = reader
        self._uow = uow

    async def __call__(self, data: SetProjectRemindersDTO) -> ProjectDTO:
        """Replace the moving date reminders (days before the move) as a whole.

        Removed offsets cancel their pending notifications, added ones are queued
        for every member. The same set again changes and writes nothing.

        Raises:
            InvalidMovingReminderOffsetError: an offset is out of range.
            ProjectNotFoundError: no such project, or the user is not its member.
        """
        offsets = [MovingReminderOffset(days) for days in data.offsets_days]
        project = await project_of_member(
            self._projects, data.project_id, data.user_id, for_update=True
        )

        change = project.replace_moving_reminders(offsets)
        if change:
            await self._projects.update(project)
            if change.removed:
                await self._notifications.cancel_pending_moving_reminders(
                    project.id, offsets=change.removed
                )
            await self._reminders.schedule(
                project, members=project.member_ids, offsets=change.added, now=utc_now()
            )
            await self._uow.commit()
            logger.info(
                "project_reminders_changed",
                extra={
                    "project_id": str(project.id),
                    "project_name": project.name.value,
                    "user_id": str(data.user_id),
                    "offsets_days": sorted(offset.days for offset in project.moving_reminders),
                },
            )
        return await read_project_or_raise(self._reader, project.id, data.user_id)
