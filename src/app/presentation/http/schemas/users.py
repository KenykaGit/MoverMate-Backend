from typing import Self
from uuid import UUID

from pydantic import BaseModel, SecretStr

from app.application.users.dto import UserDTO
from app.presentation.http.schemas.common import RequestSchema


class UserResponse(BaseModel):
    id: UUID
    username: str

    @classmethod
    def from_dto(cls, user: UserDTO) -> Self:
        return cls(id=user.id, username=user.username)


class ChangePasswordRequest(RequestSchema):
    current_password: SecretStr
    new_password: SecretStr


class ChangeUsernameRequest(RequestSchema):
    new_username: str
