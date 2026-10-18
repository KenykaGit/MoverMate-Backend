import logging

from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.user_repository import AbstractUserRepository
from app.application.users.dto import ChangeUsernameDTO, UserDTO
from app.application.users.lookup import get_active_user_or_raise
from app.domain.users.value_objects import Username

logger = logging.getLogger(__name__)


class ChangeUsername:
    def __init__(self, user_repository: AbstractUserRepository, uow: AbstractUnitOfWork) -> None:
        self._user_repository = user_repository
        self._uow = uow

    async def __call__(self, data: ChangeUsernameDTO) -> UserDTO:
        """Change the username (usernames are public, so no password is needed).

        Raises:
            InvalidUsernameError: the new username is malformed.
            UserNotFoundError: there is no such active user.
            UsernameAlreadyTakenError: another active user has the new username.
        """
        new_username = Username(data.new_username)
        user = await get_active_user_or_raise(self._user_repository, data.user_id)

        previous = user.username
        user.change_username(new_username)
        await self._user_repository.update(user)
        await self._uow.commit()

        if previous != user.username:
            logger.info(
                "username_changed",
                extra={
                    "user_id": str(user.id),
                    "username": user.username.value,
                    "previous_username": previous.value,
                },
            )
        return UserDTO.from_entity(user)
