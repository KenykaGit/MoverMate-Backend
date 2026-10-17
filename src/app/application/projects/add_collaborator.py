import logging

from app.application.common.clock import utc_now
from app.application.common.uow import AbstractUnitOfWork
from app.application.exceptions import UserNotFoundError
from app.application.interfaces.project_reader import AbstractProjectReader
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.user_repository import AbstractUserRepository
from app.application.projects.dto import AddCollaboratorDTO, AddedCollaboratorDTO
from app.application.projects.lookup import get_member_project_or_raise
from app.application.projects.services import MovingReminderScheduler
from app.domain.users.value_objects import Username

logger = logging.getLogger(__name__)


class AddCollaborator:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        users: AbstractUserRepository,
        reminders: MovingReminderScheduler,
        reader: AbstractProjectReader,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._users = users
        self._reminders = reminders
        self._reader = reader
        self._uow = uow

    async def __call__(self, data: AddCollaboratorDTO) -> AddedCollaboratorDTO:
        """Add an active user to the project by username; any member may.

        Idempotent: adding a member again changes nothing (is_new is False). The
        newcomer gets the project's moving date reminders.

        Raises:
            InvalidUsernameError: the username is malformed.
            ProjectNotFoundError: no such project, or the caller is not its member.
            UserNotFoundError: there is no active user with this username.
        """
        username = Username(data.username)
        project = await get_member_project_or_raise(
            self._projects, data.project_id, data.user_id, for_update=True
        )
        user = await self._users.get_by_username(username)
        if user is None:
            raise UserNotFoundError(username)

        is_new = not project.is_member(user.id)
        if is_new:
            project.add_member(user.id)
            await self._projects.update(project)
            await self._reminders.schedule(
                project, members={user.id}, offsets=project.moving_reminders, now=utc_now()
            )
            await self._uow.commit()
            logger.info(
                "collaborator_added",
                extra={
                    "project_id": str(project.id),
                    "user_id": str(data.user_id),
                    "collaborator_id": str(user.id),
                },
            )

        collaborator = await self._reader.get_collaborator(project.id, user.id)
        if collaborator is None:  # pragma: no cover  # the user was deleted meanwhile
            raise UserNotFoundError(username)
        return AddedCollaboratorDTO(collaborator=collaborator, is_new=is_new)
