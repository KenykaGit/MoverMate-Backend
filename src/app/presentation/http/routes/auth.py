from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, status

from app.application.auth.login import Login
from app.application.auth.logout import Logout
from app.application.auth.refresh_tokens import RefreshTokens
from app.application.common.clock import utc_now
from app.application.users.register_user import RegisterUser
from app.presentation.http.errors import error_doc
from app.presentation.http.rate_limit import auth_rate_limit
from app.presentation.http.schemas.auth import (
    LoginRequest,
    RefreshTokenRequest,
    RegisterRequest,
    TokenPairResponse,
)
from app.presentation.http.schemas.users import UserResponse

router = APIRouter(prefix="/auth", tags=["auth"], route_class=DishkaRoute)

_UNAUTHORIZED = error_doc(status.HTTP_401_UNAUTHORIZED, "Rejected")


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(auth_rate_limit)],
    responses=error_doc(status.HTTP_409_CONFLICT, "Email or username taken"),
)
async def register(body: RegisterRequest, register_user: FromDishka[RegisterUser]) -> UserResponse:
    user = await register_user(body.to_dto())
    return UserResponse.from_dto(user)


@router.post("/login", dependencies=[Depends(auth_rate_limit)], responses=_UNAUTHORIZED)
async def login(body: LoginRequest, login: FromDishka[Login]) -> TokenPairResponse:
    tokens = await login(body.to_dto())
    return TokenPairResponse.from_dto(tokens, now=utc_now())


@router.post("/refresh", dependencies=[Depends(auth_rate_limit)], responses=_UNAUTHORIZED)
async def refresh(
    body: RefreshTokenRequest, refresh_tokens: FromDishka[RefreshTokens]
) -> TokenPairResponse:
    """Exchange a refresh token for a new pair; the old one stops working.

    Presenting an already used refresh token ends every session of the user.
    """
    tokens = await refresh_tokens(body.refresh_token.get_secret_value())
    return TokenPairResponse.from_dto(tokens, now=utc_now())


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshTokenRequest, logout: FromDishka[Logout]) -> None:
    """Revoke the refresh token. The access token works until it expires."""
    await logout(body.refresh_token.get_secret_value())
