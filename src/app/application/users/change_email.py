from app.application.common.uow import AbstractUnitOfWork
from app.application.exceptions import InvalidCredentialsError
from app.application.interfaces.password_hasher import AbstractPasswordHasher
from app.application.interfaces.user_repository import AbstractUserRepository
from app.application.users.dto import ChangeEmailDTO, UserDTO
from app.application.users.lookup import get_active_user_or_raise
from app.domain.users.value_objects import Email


class ChangeEmail:
    def __init__(
        self,
        user_repository: AbstractUserRepository,
        password_hasher: AbstractPasswordHasher,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._user_repository = user_repository
        self._password_hasher = password_hasher
        self._uow = uow

    async def __call__(self, data: ChangeEmailDTO) -> UserDTO:
        """Change the email; requires the current password.

        Raises:
            InvalidEmailError: the new email is malformed.
            UserNotFoundError: there is no such active user.
            InvalidCredentialsError: the password is wrong.
            EmailAlreadyTakenError: another active user has the new email.
        """
        new_email = Email(data.new_email)
        user = await get_active_user_or_raise(self._user_repository, data.user_id)

        # Password first, uniqueness second: otherwise a stolen access token
        # could probe which emails are registered.
        if not await self._password_hasher.verify(data.password, user.password_hash):
            raise InvalidCredentialsError

        user.change_email(new_email)
        await self._user_repository.update(user)
        await self._uow.commit()
        return UserDTO.from_entity(user)
