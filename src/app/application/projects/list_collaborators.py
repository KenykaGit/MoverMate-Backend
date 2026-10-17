from app.application.interfaces.project_reader import AbstractProjectReader, CollaboratorDTO
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.projects.dto import ProjectActionDTO
from app.application.projects.lookup import get_member_project_or_raise


class ListCollaborators:
    def __init__(self, projects: AbstractProjectRepository, reader: AbstractProjectReader) -> None:
        self._projects = projects
        self._reader = reader

    async def __call__(self, data: ProjectActionDTO) -> list[CollaboratorDTO]:
        """Members of the project, the owner first.

        Raises:
            ProjectNotFoundError: no such project, or the user is not its member.
        """
        project = await get_member_project_or_raise(self._projects, data.project_id, data.user_id)
        return await self._reader.list_collaborators(project.id)
