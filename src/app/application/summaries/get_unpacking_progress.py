from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.inventory.lookup import project_of_member
from app.application.summaries.dto import ProjectViewDTO, UnpackingProgressRoomDTO
from app.application.summaries.read_model import AbstractSummaryReadModel


class GetUnpackingProgress:
    def __init__(
        self, projects: AbstractProjectRepository, read_model: AbstractSummaryReadModel
    ) -> None:
        self._projects = projects
        self._read_model = read_model

    async def __call__(self, data: ProjectViewDTO) -> list[UnpackingProgressRoomDTO]:
        """Rooms of the new home: unpacked boxes out of all boxes headed for each.

        Raises:
            ProjectNotFoundError: no such project, or the user is not its member.
        """
        project = await project_of_member(self._projects, data.project_id, data.user_id)
        return await self._read_model.unpacking_progress(project.id)
