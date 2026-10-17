from app.application.interfaces.project_reader import AbstractProjectReader, ProjectDTO
from app.application.projects.dto import ProjectActionDTO
from app.application.projects.lookup import read_project_or_raise


class GetProject:
    def __init__(self, reader: AbstractProjectReader) -> None:
        self._reader = reader

    async def __call__(self, data: ProjectActionDTO) -> ProjectDTO:
        """Raises:
        ProjectNotFoundError: no such project, or the user is not its member.
        """
        return await read_project_or_raise(self._reader, data.project_id, data.user_id)
