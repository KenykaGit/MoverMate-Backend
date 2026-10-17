"""Who may see a project: its members only.

To someone who is not a member, a project and everything in it do not exist, so
every miss is a 404 of the object that was asked for, never a 403
(api-endpoints.md, 1.3). Helpers only read; the calling use case owns the transaction.
"""

from uuid import UUID

from app.application.exceptions import ProjectNotFoundError
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.domain.common.ids import ProjectId, UserId
from app.domain.projects.entity import Project


async def member_project(
    projects: AbstractProjectRepository,
    project_id: UUID,
    user_id: UUID,
    *,
    for_update: bool = False,
) -> Project | None:
    """The active project of which `user_id` is a member, or None."""
    project = await projects.get_by_id(ProjectId(project_id), for_update=for_update)
    if project is None or not project.is_member(UserId(user_id)):
        return None
    return project


async def project_of_member(
    projects: AbstractProjectRepository,
    project_id: UUID,
    user_id: UUID,
    *,
    for_update: bool = False,
) -> Project:
    """Like `member_project`, for a project the caller asked for by id.

    Raises:
        ProjectNotFoundError: no such project, or not a member (indistinguishable).
    """
    project = await member_project(projects, project_id, user_id, for_update=for_update)
    if project is None:
        raise ProjectNotFoundError(project_id)
    return project
