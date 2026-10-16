from dataclasses import dataclass, field
from typing import Self
from uuid import UUID

from app.domain.users.entity import User


@dataclass(frozen=True, slots=True, kw_only=True)
class RegisterUserDTO:
    username: str
    password: str = field(repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class ChangePasswordDTO:
    user_id: UUID
    current_password: str = field(repr=False)
    new_password: str = field(repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class ChangeUsernameDTO:
    user_id: UUID
    new_username: str


@dataclass(frozen=True, slots=True, kw_only=True)
class UserDTO:
    """Public view of a user.

    Never exposes the password hash, nor the soft-delete flag: use cases
    return active users only.
    """

    id: UUID
    username: str

    @classmethod
    def from_entity(cls, user: User) -> Self:
        return cls(id=user.id, username=user.username.value)
