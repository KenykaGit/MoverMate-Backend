import logging

from app.application.common.clock import utc_now
from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.notification_repository import AbstractNotificationRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.task_repository import AbstractTaskRepository
from app.application.tasks.dto import SetTaskAssigneesDTO, TaskDTO
from app.application.tasks.lookup import assignees_in, task_of_member, task_view
from app.application.tasks.read_model import AbstractTaskReadModel
from app.application.tasks.services import TaskNotifier
from app.domain.common.ids import UserId

logger = logging.getLogger(__name__)


class SetTaskAssignees:
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

    async def __call__(self, data: SetTaskAssigneesDTO) -> TaskDTO:
        """Replace the assignees as a whole.

        Removed assignees lose their pending notifications about the task. Added
        ones are told (TASK_ASSIGNED, except whoever assigned themselves) and get
        the reminders of the task. The same set again changes and writes nothing.

        Raises:
            TaskNotFoundError: no such active task in a project of the caller.
            AssigneeNotCollaboratorError: an assignee is not a member of the project.
        """
        task, project = await task_of_member(
            self._tasks, self._projects, data.task_id, data.user_id, for_update=True
        )
        wanted = assignees_in(project, data.assignee_ids)
        added = wanted - task.assignee_ids
        removed = task.assignee_ids - wanted

        if added or removed:
            for user_id in removed:
                task.unassign(user_id)
            for user_id in added:
                task.assign(user_id)
            await self._tasks.update(task)
            for user_id in sorted(removed):
                await self._notifications.cancel_pending_for_task(task.id, user_id=user_id)
            now = utc_now()
            await self._notifier.assigned(task, added, by=UserId(data.user_id), now=now)
            await self._notifier.schedule_reminders(
                task, assignees=added, offsets=task.reminders, now=now
            )

        view = await task_view(self._read_model, task.id)
        await self._uow.commit()
        if added or removed:
            logger.info(
                "task_assignees_changed",
                extra={
                    "task_id": str(task.id),
                    "added": sorted(str(user_id) for user_id in added),
                    "removed": sorted(str(user_id) for user_id in removed),
                },
            )
        return view
