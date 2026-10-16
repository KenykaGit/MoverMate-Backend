import logging

from app.application.common.clock import utc_now
from app.application.common.uow import AbstractUnitOfWork
from app.application.exceptions import InvalidCredentialsError
from app.application.interfaces.password_hasher import AbstractPasswordHasher
from app.application.interfaces.refresh_token_repository import AbstractRefreshTokenRepository
from app.application.interfaces.user_repository import AbstractUserRepository
from app.application.users.dto import ChangePasswordDTO
from app.application.users.lookup import get_active_user_or_raise
from app.domain.users.value_objects import RawPassword

logger = logging.getLogger(__name__)


class ChangePassword:
    def __init__(
        self,
        user_repository: AbstractUserRepository,
        refresh_token_repository: AbstractRefreshTokenRepository,
        password_hasher: AbstractPasswordHasher,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._user_repository = user_repository
        self._refresh_token_repository = refresh_token_repository
        self._password_hasher = password_hasher
        self._uow = uow

    async def __call__(self, data: ChangePasswordDTO) -> None:
        """Change the password; requires the current one.

        Ends every session of the user: whoever knew the old password may
        have logged in with it.

        Raises:
            InvalidPasswordError: the new password violates the policy.
            UserNotFoundError: there is no such active user.
            InvalidCredentialsError: the current password is wrong.
        """
        new_password = RawPassword(data.new_password)
        user = await get_active_user_or_raise(self._user_repository, data.user_id)

        if not await self._password_hasher.verify(data.current_password, user.password_hash):
            raise InvalidCredentialsError

        user.change_password(await self._password_hasher.hash(new_password))
        await self._user_repository.update(user)
        await self._refresh_token_repository.revoke_all_for_user(user.id, utc_now())
        await self._uow.commit()

        logger.info("password_changed", extra={"user_id": str(user.id)})
