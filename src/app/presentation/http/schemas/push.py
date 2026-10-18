from uuid import UUID

from pydantic import BaseModel, Field

from app.application.notifications.dto import SubscribeDTO
from app.presentation.http.schemas.common import RequestSchema


class VapidPublicKeyResponse(BaseModel):
    public_key: str = Field(
        description="applicationServerKey for pushManager.subscribe() (base64url, P-256)"
    )


class PushKeysRequest(RequestSchema):
    p256dh: str
    auth: str


class PushSubscriptionRequest(RequestSchema):
    """`PushSubscription.toJSON()` of the browser, as is."""

    endpoint: str
    keys: PushKeysRequest
    expiration_time: float | None = Field(
        default=None,
        alias="expirationTime",
        description="Sent by browsers; ignored: push services report expired endpoints",
    )

    def to_dto(self, *, user_id: UUID) -> SubscribeDTO:
        return SubscribeDTO(
            user_id=user_id,
            endpoint=self.endpoint,
            p256dh=self.keys.p256dh,
            auth=self.keys.auth,
        )
