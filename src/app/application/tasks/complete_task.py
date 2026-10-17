import logging

from app.application.common.clock import utc_now
from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.audit_log_repository import AbstractAuditLogRepository
from app.application.interfaces.notification_repository import AbstractNotificationRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.task_repository import AbstractTaskRepository
from app.application.tasks.dto import TaskActionDTO, TaskDTO
from app.application.tasks.lookup import task_of_member, task_view
from app.application.tasks.read_model import AbstractTaskReadModel
from app.domain.audit.entity import AuditAction, AuditEntry
from app.domain.common.ids import UserId

logger = logging.getLogger(__name__)


class CompleteTask:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        tasks: AbstractTaskRepository,
        notifications: AbstractNotificationRepository,
        audit_log: AbstractAuditLogRepository,
        read_model: AbstractTaskReadModel,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._tasks = tasks
        self._notifications = notifications
        self._audit_log = audit_log
        self._read_model = read_model
        self._uow = uow

    async def __call__(self, data: TaskActionDTO) -> TaskDTO:
        """Mark the task done and cancel its pending notifications.

        Idempotent: a task already done keeps its first completion time, and
        nothing is written.

        Raises:
            TaskNotFoundError: no such active task in a project of the caller.
        """
        task, _ = await task_of_member(
            self._tasks, self._projects, data.task_id, data.user_id, for_update=True
        )
        now = utc_now()
        completed = task.complete(now=now)
        if completed:
            await self._tasks.update(task)
            await self._notifications.cancel_pending_for_task(task.id)
            await self._audit_log.add(
                AuditEntry.record(
                    project_id=task.project_id,
                    actor_id=UserId(data.user_id),
                    action=AuditAction.TASK_COMPLETED,
                    entity_id=task.id,
                    now=now,
                )
            )
        view = await task_view(self._read_model, task.id)
        await self._uow.commit()
        if completed:
            logger.info(
                "task_completed",
                extra={"project_id": str(task.project_id), "task_id": str(task.id)},
            )
        return view
