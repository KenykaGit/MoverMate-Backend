import logging
from uuid import UUID

from app.application.common.clock import utc_now
from app.application.common.uow import AbstractUnitOfWork
from app.application.exceptions import UserOwnsProjectsError
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.refresh_token_repository import AbstractRefreshTokenRepository
from app.application.interfaces.user_repository import AbstractUserRepository
from app.domain.common.ids import UserId

logger = logging.getLogger(__name__)


class SoftDeleteUser:
    def __init__(
        self,
        user_repository: AbstractUserRepository,
        refresh_token_repository: AbstractRefreshTokenRepository,
        project_repository: AbstractProjectRepository,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._user_repository = user_repository
        self._refresh_token_repository = refresh_token_repository
        self._project_repository = project_repository
        self._uow = uow

    async def __call__(self, user_id: UUID) -> None:
        """Soft-delete a user and end all their sessions.

        The data is kept and can be restored (sessions are not). Access tokens
        stop working at once: every request re-reads the user. Idempotent:
        deleting a missing or already deleted user is not an error.

        Memberships in other people's projects stay (hidden while the account is
        deleted), so that restoring the user restores them too.

        Raises:
            UserOwnsProjectsError: the user owns active projects; they would be left
                without an owner.
        """
        user = await self._user_repository.get_by_id(UserId(user_id))
        if user is None:
            return
        if await self._project_repository.owns_active_projects(user.id):
            raise UserOwnsProjectsError

        user.soft_delete()
        await self._user_repository.update(user)
        await self._refresh_token_repository.revoke_all_for_user(user.id, utc_now())
        await self._uow.commit()

        logger.info("user_deleted", extra={"user_id": str(user.id)})
