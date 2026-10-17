from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.task_repository import AbstractTaskRepository
from app.application.tasks.dto import TaskActionDTO, TaskDTO
from app.application.tasks.lookup import task_of_member, task_view
from app.application.tasks.read_model import AbstractTaskReadModel


class GetTask:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        tasks: AbstractTaskRepository,
        read_model: AbstractTaskReadModel,
    ) -> None:
        self._projects = projects
        self._tasks = tasks
        self._read_model = read_model

    async def __call__(self, data: TaskActionDTO) -> TaskDTO:
        """Raises TaskNotFoundError: no such active task in a project of the caller."""
        task, _ = await task_of_member(self._tasks, self._projects, data.task_id, data.user_id)
        return await task_view(self._read_model, task.id)
