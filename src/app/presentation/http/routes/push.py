from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Query, status

from app.application.notifications.dto import UnsubscribeDTO
from app.application.notifications.get_vapid_public_key import GetVapidPublicKey
from app.application.notifications.subscribe_to_push import SubscribeToPush
from app.application.notifications.unsubscribe_from_push import UnsubscribeFromPush
from app.presentation.http.auth import CurrentUser
from app.presentation.http.errors import error_doc
from app.presentation.http.schemas.push import PushSubscriptionRequest, VapidPublicKeyResponse

router = APIRouter(prefix="/push", tags=["push"], route_class=DishkaRoute)

_UNAUTHORIZED = error_doc(status.HTTP_401_UNAUTHORIZED, "Not authenticated")


@router.get("/vapid-public-key", operation_id="getVapidPublicKey")
async def get_vapid_public_key(
    get_public_key: FromDishka[GetVapidPublicKey],
) -> VapidPublicKeyResponse:
    """The server's VAPID key; needed before the browser can subscribe."""
    return VapidPublicKeyResponse(public_key=await get_public_key())


@router.put(
    "/subscriptions",
    operation_id="upsertPushSubscription",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_UNAUTHORIZED,
)
async def upsert_push_subscription(
    body: PushSubscriptionRequest, user: CurrentUser, subscribe: FromDishka[SubscribeToPush]
) -> None:
    """Send `PushSubscription.toJSON()` after every subscribe and on app start:
    upsert by endpoint, the browser then gets the current user's notifications."""
    await subscribe(body.to_dto(user_id=user.id))


@router.delete(
    "/subscriptions",
    operation_id="deletePushSubscription",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_UNAUTHORIZED,
)
async def delete_push_subscription(
    endpoint: Annotated[str, Query(max_length=4096, description="The browser's endpoint")],
    user: CurrentUser,
    unsubscribe: FromDishka[UnsubscribeFromPush],
) -> None:
    """Stop pushing to this browser (call it on logout too). Always 204."""
    await unsubscribe(UnsubscribeDTO(user_id=user.id, endpoint=endpoint))
