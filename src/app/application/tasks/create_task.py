import logging

from app.application.common.clock import utc_now
from app.application.common.membership import project_of_member
from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.audit_log_repository import AbstractAuditLogRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.task_repository import AbstractTaskRepository
from app.application.tasks.dto import CreateTaskDTO, TaskDTO
from app.application.tasks.lookup import assignees_in, task_view
from app.application.tasks.read_model import AbstractTaskReadModel
from app.application.tasks.services import TaskNotifier
from app.domain.audit.entity import AuditAction, AuditEntry
from app.domain.common.ids import UserId
from app.domain.common.reminders import ReminderOffset
from app.domain.tasks.entity import Task
from app.domain.tasks.value_objects import TaskDescription, TaskTitle

logger = logging.getLogger(__name__)


class CreateTask:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        tasks: AbstractTaskRepository,
        notifier: TaskNotifier,
        audit_log: AbstractAuditLogRepository,
        read_model: AbstractTaskReadModel,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._tasks = tasks
        self._notifier = notifier
        self._audit_log = audit_log
        self._read_model = read_model
        self._uow = uow

    async def __call__(self, data: CreateTaskDTO) -> TaskDTO:
        """A new task, not done, with its assignees and reminders.

        Assignees other than the creator are told (TASK_ASSIGNED); every assignee
        gets a TASK_REMINDER per offset whose moment is still ahead.

        Raises:
            InvalidTaskTitleError, InvalidTaskDescriptionError: empty or too long.
            InvalidDueDateError: the due time has no time zone.
            InvalidReminderOffsetError: an offset is out of 1..8760 hours.
            ReminderWithoutDueDateError: reminders without a due time.
            ProjectNotFoundError: no such project, or the caller is not its member.
            AssigneeNotCollaboratorError: an assignee is not a member of the project.
        """
        title = TaskTitle(data.title)
        description = None if data.description is None else TaskDescription(data.description)
        offsets = [ReminderOffset(hours) for hours in data.reminder_offsets_hours]
        project = await project_of_member(self._projects, data.project_id, data.user_id)
        assignees = assignees_in(project, data.assignee_ids)

        task = Task.create(
            project_id=project.id,
            title=title,
            description=description,
            phase=data.phase,
            due_at=data.due_at,
        )
        for offset in offsets:
            task.add_reminder(offset)
        for user_id in assignees:
            task.assign(user_id)
        await self._tasks.add(task)

        now = utc_now()
        creator = UserId(data.user_id)
        await self._notifier.assigned(task, task.assignee_ids, by=creator, now=now)
        await self._notifier.schedule_reminders(
            task, assignees=task.assignee_ids, offsets=task.reminders, now=now
        )
        await self._audit_log.add(
            AuditEntry.record(
                project_id=project.id,
                actor_id=creator,
                action=AuditAction.TASK_CREATED,
                entity_id=task.id,
                now=now,
            )
        )

        view = await task_view(self._read_model, task.id)
        await self._uow.commit()
        logger.info(
            "task_created",
            extra={
                "project_id": str(project.id),
                "task_id": str(task.id),
                "assignees": len(task.assignee_ids),
            },
        )
        return view
