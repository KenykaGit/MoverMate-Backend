import logging

from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.notification_repository import AbstractNotificationRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.projects.dto import RemoveCollaboratorDTO
from app.application.projects.lookup import get_member_project_or_raise
from app.domain.common.ids import UserId

logger = logging.getLogger(__name__)


class RemoveCollaborator:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        notifications: AbstractNotificationRepository,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._notifications = notifications
        self._uow = uow

    async def __call__(self, data: RemoveCollaboratorDTO) -> None:
        """Remove a member; any member may, and removing oneself is leaving.

        The member's task assignments go with the membership (a cascading foreign
        key), their pending notifications in the project are cancelled.
        Idempotent: removing a non-member changes nothing.

        Raises:
            ProjectNotFoundError: no such project, or the caller is not its member.
            CannotRemoveOwnerError: the owner never leaves their project.
        """
        project = await get_member_project_or_raise(
            self._projects, data.project_id, data.user_id, for_update=True
        )
        collaborator_id = UserId(data.collaborator_id)
        was_member = project.is_member(collaborator_id)
        project.remove_member(collaborator_id)
        if not was_member:
            return

        await self._projects.update(project)
        await self._notifications.cancel_pending(project.id, user_id=collaborator_id)
        await self._uow.commit()

        event = "project_left" if collaborator_id == data.user_id else "collaborator_removed"
        logger.info(
            event,
            extra={
                "project_id": str(project.id),
                "user_id": str(data.user_id),
                "collaborator_id": str(collaborator_id),
            },
        )
