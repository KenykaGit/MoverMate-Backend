import logging
from uuid import UUID

from app.application.common.clock import utc_now
from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.refresh_token_repository import AbstractRefreshTokenRepository
from app.application.interfaces.user_repository import AbstractUserRepository
from app.domain.common.ids import UserId

logger = logging.getLogger(__name__)


class SoftDeleteUser:
    def __init__(
        self,
        user_repository: AbstractUserRepository,
        refresh_token_repository: AbstractRefreshTokenRepository,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._user_repository = user_repository
        self._refresh_token_repository = refresh_token_repository
        self._uow = uow

    async def __call__(self, user_id: UUID) -> None:
        """Soft-delete a user and end all their sessions.

        The data is kept and can be restored (sessions are not). Access tokens
        stop working at once: every request re-reads the user. Idempotent:
        deleting a missing or already deleted user is not an error.
        """
        user = await self._user_repository.get_by_id(UserId(user_id))
        if user is None:
            return

        user.soft_delete()
        await self._user_repository.update(user)
        await self._refresh_token_repository.revoke_all_for_user(user.id, utc_now())
        await self._uow.commit()

        logger.info("user_deleted", extra={"user_id": str(user.id)})
