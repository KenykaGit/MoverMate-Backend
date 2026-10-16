from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, status

from app.application.users.change_password import ChangePassword
from app.application.users.change_username import ChangeUsername
from app.application.users.dto import ChangePasswordDTO, ChangeUsernameDTO
from app.application.users.soft_delete_user import SoftDeleteUser
from app.presentation.http.auth import CurrentUser
from app.presentation.http.errors import error_doc
from app.presentation.http.schemas.users import (
    ChangePasswordRequest,
    ChangeUsernameRequest,
    UserResponse,
)

router = APIRouter(
    prefix="/users",
    tags=["users"],
    route_class=DishkaRoute,
    responses=error_doc(status.HTTP_401_UNAUTHORIZED, "Not authenticated"),
)

_CONFLICT = error_doc(status.HTTP_409_CONFLICT, "Username taken")


@router.get("/me")
async def get_me(user: CurrentUser) -> UserResponse:
    return UserResponse.from_dto(user)


@router.put("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest, user: CurrentUser, change_password: FromDishka[ChangePassword]
) -> None:
    """Ends every session: log in again with the new password."""
    await change_password(
        ChangePasswordDTO(
            user_id=user.id,
            current_password=body.current_password.get_secret_value(),
            new_password=body.new_password.get_secret_value(),
        )
    )


@router.put("/me/username", responses=_CONFLICT)
async def change_username(
    body: ChangeUsernameRequest, user: CurrentUser, change_username: FromDishka[ChangeUsername]
) -> UserResponse:
    changed = await change_username(
        ChangeUsernameDTO(user_id=user.id, new_username=body.new_username)
    )
    return UserResponse.from_dto(changed)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(user: CurrentUser, soft_delete_user: FromDishka[SoftDeleteUser]) -> None:
    """Soft-delete the account and end every session; the username
    becomes free at once."""
    await soft_delete_user(user.id)
