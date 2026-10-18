import logging

from app.application.common.uow import AbstractUnitOfWork
from app.application.exceptions import UsernameAlreadyTakenError
from app.application.interfaces.password_hasher import AbstractPasswordHasher
from app.application.interfaces.user_repository import AbstractUserRepository
from app.application.users.dto import RegisterUserDTO, UserDTO
from app.domain.users.entity import User
from app.domain.users.value_objects import RawPassword, Username

logger = logging.getLogger(__name__)


class RegisterUser:
    def __init__(
        self,
        user_repository: AbstractUserRepository,
        password_hasher: AbstractPasswordHasher,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._user_repository = user_repository
        self._password_hasher = password_hasher
        self._uow = uow

    async def __call__(self, data: RegisterUserDTO) -> UserDTO:
        """Register a new active user.

        Raises:
            DomainError: the username or password is invalid.
            UsernameAlreadyTakenError: an active user has this username.
        """
        username = Username(data.username)
        password = RawPassword(data.password)

        # Fail fast before the deliberately slow hashing. Not race-safe on its
        # own: add() still rejects a concurrent duplicate.
        if await self._user_repository.get_by_username(username) is not None:
            raise UsernameAlreadyTakenError(username)

        user = User.register(
            username=username,
            password_hash=await self._password_hasher.hash(password),
        )
        await self._user_repository.add(user)
        await self._uow.commit()

        logger.info(
            "user_registered",
            extra={"user_id": str(user.id), "username": user.username.value},
        )
        return UserDTO.from_entity(user)
