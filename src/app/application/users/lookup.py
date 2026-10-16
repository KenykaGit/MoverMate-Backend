from uuid import UUID

from app.application.exceptions import UserNotFoundError
from app.application.interfaces.user_repository import AbstractUserRepository
from app.domain.common.ids import UserId
from app.domain.users.entity import User


async def get_active_user_or_raise(repository: AbstractUserRepository, user_id: UUID) -> User:
    user = await repository.get_by_id(UserId(user_id))
    if user is None:
        raise UserNotFoundError(user_id)
    return user
