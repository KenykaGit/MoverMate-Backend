import logging
from uuid import UUID

from app.application.common.uow import AbstractUnitOfWork
from app.application.exceptions import UserNotFoundError
from app.application.interfaces.user_repository import AbstractUserRepository
from app.application.users.dto import UserDTO
from app.domain.common.ids import UserId

logger = logging.getLogger(__name__)


class RestoreUser:
    """Operator scenario (support tooling); not exposed over the public API."""

    def __init__(self, user_repository: AbstractUserRepository, uow: AbstractUnitOfWork) -> None:
        self._user_repository = user_repository
        self._uow = uow

    async def __call__(self, user_id: UUID) -> UserDTO:
        """Undo a soft delete. Idempotent for an active user.

        Raises:
            UserNotFoundError: there is no user with this id at all.
            EmailAlreadyTakenError: the email was taken while the user was deleted.
            UsernameAlreadyTakenError: same for the username.
        """
        user = await self._user_repository.get_by_id(UserId(user_id), include_deleted=True)
        if user is None:
            raise UserNotFoundError(user_id)
        if user.is_active:
            return UserDTO.from_entity(user)

        user.restore()
        await self._user_repository.update(user)
        await self._uow.commit()

        logger.info("user_restored", extra={"user_id": str(user.id)})
        return UserDTO.from_entity(user)
