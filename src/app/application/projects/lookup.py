from uuid import UUID

from app.application.exceptions import ProjectNotFoundError
from app.application.interfaces.project_reader import AbstractProjectReader, ProjectDTO
from app.domain.common.ids import ProjectId, UserId


async def read_project_or_raise(
    reader: AbstractProjectReader, project_id: UUID, viewer_id: UUID
) -> ProjectDTO:
    """The project as `viewer_id` sees it.

    Raises:
        ProjectNotFoundError: no such project, or not a member (indistinguishable).
    """
    project = await reader.get(ProjectId(project_id), viewer_id=UserId(viewer_id))
    if project is None:
        raise ProjectNotFoundError(project_id)
    return project
