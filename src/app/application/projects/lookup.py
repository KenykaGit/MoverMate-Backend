from uuid import UUID

from app.application.exceptions import ProjectNotFoundError
from app.application.interfaces.project_reader import AbstractProjectReader, ProjectDTO
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.domain.common.ids import ProjectId, UserId
from app.domain.projects.entity import Project


async def get_member_project_or_raise(
    repository: AbstractProjectRepository,
    project_id: UUID,
    member_id: UUID,
    *,
    for_update: bool = False,
) -> Project:
    """The active project of which `member_id` is a member.

    Raises:
        ProjectNotFoundError: no such project, or not a member (indistinguishable).
    """
    project = await repository.get_by_id(ProjectId(project_id), for_update=for_update)
    if project is None or not project.is_member(UserId(member_id)):
        raise ProjectNotFoundError(project_id)
    return project


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
