from abc import ABC, abstractmethod

from app.domain.common.ids import UserId
from app.domain.users.entity import User
from app.domain.users.value_objects import Username


class AbstractUserRepository(ABC):
    """Collection-like access to persisted users.

    Users are soft-deleted (User.is_active is False): deleted users are hidden
    from every lookup unless explicitly requested, and their username
    is free to be taken by others.

    Implementations must not commit: the unit of work owns the transaction.
    """

    @abstractmethod
    async def add(self, user: User) -> None:
        """Stage a new user.

        Uniqueness among active users is enforced even against concurrent
        transactions, so it is safe to rely on it instead of a prior lookup.

        Raises:
            UsernameAlreadyTakenError: an active user has this username.
        """

    @abstractmethod
    async def get_by_id(self, user_id: UserId, *, include_deleted: bool = False) -> User | None:
        """The user with this id, or None."""

    @abstractmethod
    async def get_by_username(self, username: Username) -> User | None:
        """The active user with this username, or None."""

    @abstractmethod
    async def update(self, user: User) -> None:
        """Stage changes of an existing user, deleted or not (also soft delete/restore).

        Raises:
            UserNotFoundError: there is no user with this id.
            UsernameAlreadyTakenError: another active user has the username.
        """
