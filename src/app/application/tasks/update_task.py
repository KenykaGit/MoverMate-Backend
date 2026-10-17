from app.application.common.clock import utc_now
from app.application.common.patch import UNSET
from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.task_repository import AbstractTaskRepository
from app.application.tasks.dto import TaskDTO, UpdateTaskDTO
from app.application.tasks.lookup import task_of_member, task_view
from app.application.tasks.read_model import AbstractTaskReadModel
from app.application.tasks.services import TaskNotifier
from app.domain.tasks.value_objects import TaskDescription, TaskTitle


class UpdateTask:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        tasks: AbstractTaskRepository,
        notifier: TaskNotifier,
        read_model: AbstractTaskReadModel,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._tasks = tasks
        self._notifier = notifier
        self._read_model = read_model
        self._uow = uow

    async def __call__(self, data: UpdateTaskDTO) -> TaskDTO:
        """Change the title, description, phase or due time; absent fields stay.

        A new due time moves the pending reminders (those now in the past are
        cancelled); clearing it removes the reminders of the task.

        Raises:
            TaskNotFoundError: no such active task in a project of the caller.
            InvalidTaskTitleError, InvalidTaskDescriptionError: empty or too long.
            InvalidDueDateError: the due time has no time zone.
        """
        title = None if data.title is UNSET else TaskTitle(data.title)
        description = data.description
        new_description = (
            None if description is UNSET or description is None else TaskDescription(description)
        )
        task, _ = await task_of_member(
            self._tasks, self._projects, data.task_id, data.user_id, for_update=True
        )

        if title is not None:
            task.rename(title)
        if description is not UNSET:
            task.change_description(new_description)
        if data.phase is not UNSET:
            task.move_to_phase(data.phase)
        rescheduled = data.due_at is not UNSET and data.due_at != task.due_at
        if data.due_at is not UNSET and rescheduled:
            task.reschedule(data.due_at)

        await self._tasks.update(task)
        if rescheduled:
            await self._notifier.reschedule_reminders(task, now=utc_now())
        view = await task_view(self._read_model, task.id)
        await self._uow.commit()
        return view
