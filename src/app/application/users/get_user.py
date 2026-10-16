from uuid import UUID

from app.application.interfaces.user_repository import AbstractUserRepository
from app.application.users.dto import UserDTO
from app.application.users.lookup import get_active_user_or_raise


class GetUser:
    def __init__(self, user_repository: AbstractUserRepository) -> None:
        self._user_repository = user_repository

    async def __call__(self, user_id: UUID) -> UserDTO:
        """The active user with this id.

        Raises:
            UserNotFoundError: there is no such user, or it is deleted.
        """
        user = await get_active_user_or_raise(self._user_repository, user_id)
        return UserDTO.from_entity(user)
