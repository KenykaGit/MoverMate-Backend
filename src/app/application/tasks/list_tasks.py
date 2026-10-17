from app.application.common.membership import project_of_member
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.tasks.dto import ListTasksDTO, TaskDTO
from app.application.tasks.read_model import AbstractTaskReadModel


class ListTasks:
    def __init__(
        self, projects: AbstractProjectRepository, read_model: AbstractTaskReadModel
    ) -> None:
        self._projects = projects
        self._read_model = read_model

    async def __call__(self, data: ListTasksDTO) -> list[TaskDTO]:
        """Active tasks of the project in plan order (phase, due time, id), optionally
        of one phase and done or not done only.

        Raises:
            ProjectNotFoundError: no such project, or the caller is not its member.
        """
        project = await project_of_member(self._projects, data.project_id, data.user_id)
        return await self._read_model.project_tasks(
            project.id, phase=data.phase, is_done=data.is_done
        )
