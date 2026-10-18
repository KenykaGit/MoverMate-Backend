import logging

from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.notification_repository import AbstractNotificationRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.task_repository import AbstractTaskRepository
from app.application.tasks.dto import TaskActionDTO
from app.application.tasks.lookup import task_of_member

logger = logging.getLogger(__name__)


class DeleteTask:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        tasks: AbstractTaskRepository,
        notifications: AbstractNotificationRepository,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._tasks = tasks
        self._notifications = notifications
        self._uow = uow

    async def __call__(self, data: TaskActionDTO) -> None:
        """Soft-delete the task and cancel its pending notifications.

        Raises:
            TaskNotFoundError: no such active task in a project of the caller
                (a task deleted before included).
        """
        task, _ = await task_of_member(
            self._tasks, self._projects, data.task_id, data.user_id, for_update=True
        )
        task.soft_delete()
        await self._tasks.update(task)
        await self._notifications.cancel_pending_for_task(task.id)
        await self._uow.commit()
        logger.info(
            "task_deleted",
            extra={
                "project_id": str(task.project_id),
                "task_id": str(task.id),
                "task_title": task.title.value,
                "user_id": str(data.user_id),
            },
        )
