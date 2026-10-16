from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, SecretStr

from app.application.auth.dto import LoginDTO, TokenPairDTO
from app.application.users.dto import RegisterUserDTO
from app.presentation.http.schemas.common import RequestSchema


class RegisterRequest(RequestSchema):
    email: str
    username: str
    password: SecretStr

    def to_dto(self) -> RegisterUserDTO:
        return RegisterUserDTO(
            email=self.email, username=self.username, password=self.password.get_secret_value()
        )


class LoginRequest(RequestSchema):
    login: str
    """Email or username."""
    password: SecretStr

    def to_dto(self) -> LoginDTO:
        return LoginDTO(login=self.login, password=self.password.get_secret_value())


class RefreshTokenRequest(RequestSchema):
    refresh_token: SecretStr


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105  # not a secret
    expires_in: int
    """Seconds until the access token expires (RFC 6749 §5.1)."""
    refresh_token_expires_in: int

    @classmethod
    def from_dto(cls, tokens: TokenPairDTO, *, now: datetime) -> Self:
        return cls(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_in=_seconds_until(tokens.access_token_expires_at, now),
            refresh_token_expires_in=_seconds_until(tokens.refresh_token_expires_at, now),
        )


def _seconds_until(moment: datetime, now: datetime) -> int:
    return max(0, int((moment - now).total_seconds()))
