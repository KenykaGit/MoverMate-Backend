from typing import Annotated

import structlog
from dishka.integrations.fastapi import FromDishka, inject
from fastapi import Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.application.auth.authenticate_user import AuthenticateUser
from app.application.exceptions import NotAuthenticatedError
from app.application.users.dto import UserDTO

bearer_scheme = HTTPBearer(
    auto_error=False,  # our own error format instead of FastAPI's default
    description="Access token from /api/v1/auth/login or /api/v1/auth/refresh.",
)


def bind_user_to_logs(user: UserDTO) -> None:
    """Every later line of this request says who made it (`user_id`, `username`).

    The username is the account's current one, read from the database: never
    what a client typed into a login form (that may be a mistyped password).
    """
    structlog.contextvars.bind_contextvars(user_id=str(user.id), username=user.username)


@inject
async def get_current_user(
    authenticate: FromDishka[AuthenticateUser],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)],
) -> UserDTO:
    """The active user behind the bearer token, re-read from the database on
    every request: a deleted user is rejected before the token expires."""
    if credentials is None:
        raise NotAuthenticatedError
    user = await authenticate(credentials.credentials)
    bind_user_to_logs(user)
    return user


@inject
async def get_optional_user(
    authenticate: FromDishka[AuthenticateUser],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)],
) -> UserDTO | None:
    """Like get_current_user, but no token means an anonymous caller (None).
    A token that is sent but invalid is still rejected with 401."""
    if credentials is None:
        return None
    user = await authenticate(credentials.credentials)
    bind_user_to_logs(user)
    return user


CurrentUser = Annotated[UserDTO, Depends(get_current_user)]
OptionalUser = Annotated[UserDTO | None, Depends(get_optional_user)]
