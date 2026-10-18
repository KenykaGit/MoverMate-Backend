import logging

from app.application.common.membership import project_of_member
from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.notification_repository import AbstractNotificationRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.projects.dto import ProjectActionDTO
from app.domain.common.ids import UserId
from app.domain.projects.errors import NotProjectOwnerError

logger = logging.getLogger(__name__)


class DeleteProject:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        notifications: AbstractNotificationRepository,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._notifications = notifications
        self._uow = uow

    async def __call__(self, data: ProjectActionDTO) -> None:
        """Soft-delete the project with everything in it; only the owner may.

        Pending notifications of the project are cancelled. For users the deletion
        is final: there is no way back through the API.

        Raises:
            ProjectNotFoundError: no such project (also already deleted), or not a member.
            NotProjectOwnerError: a member other than the owner tried.
        """
        project = await project_of_member(
            self._projects, data.project_id, data.user_id, for_update=True
        )
        try:
            project.soft_delete(by=UserId(data.user_id))
        except NotProjectOwnerError:
            logger.warning(
                "project_delete_forbidden",
                extra={
                    "project_id": str(project.id),
                    "project_name": project.name.value,
                    "user_id": str(data.user_id),
                },
            )
            raise
        await self._projects.update(project)
        cancelled = await self._notifications.cancel_pending(project.id)
        await self._uow.commit()

        logger.info(
            "project_deleted",
            extra={
                "project_id": str(project.id),
                "project_name": project.name.value,
                "user_id": str(data.user_id),
                "cancelled_notifications": cancelled,
            },
        )
