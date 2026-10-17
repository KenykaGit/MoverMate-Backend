from app.application.common.membership import project_of_member
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.summaries.dto import LoadingChecklistRoomDTO, ProjectViewDTO
from app.application.summaries.read_model import AbstractSummaryReadModel


class GetLoadingChecklist:
    def __init__(
        self, projects: AbstractProjectRepository, read_model: AbstractSummaryReadModel
    ) -> None:
        self._projects = projects
        self._read_model = read_model

    async def __call__(self, data: ProjectViewDTO) -> list[LoadingChecklistRoomDTO]:
        """Rooms of the old home with their boxes and whether everything is loaded.

        Raises:
            ProjectNotFoundError: no such project, or the user is not its member.
        """
        project = await project_of_member(self._projects, data.project_id, data.user_id)
        return await self._read_model.loading_checklist(project.id)
