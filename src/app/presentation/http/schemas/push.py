from dataclasses import asdict
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.application.notifications.dto import PushTestResultDTO, SendTestPushDTO, SubscribeDTO
from app.presentation.http.schemas.common import RequestSchema


class VapidPublicKeyResponse(BaseModel):
    public_key: str = Field(
        description="applicationServerKey for pushManager.subscribe() (base64url, P-256)"
    )


class PushTestRequest(RequestSchema):
    """TEMPORARY."""

    username: str = Field(description="Whose browsers to push to")
    text: str = Field(min_length=1, max_length=200, description="Body of the notification")

    def to_dto(self) -> SendTestPushDTO:
        return SendTestPushDTO(username=self.username, text=self.text)


class PushTestFailureResponse(BaseModel):
    subscription_id: UUID
    outcome: Literal["GONE", "FAILED"] = Field(
        description="GONE: the subscription no longer exists and was deactivated"
    )
    detail: str | None


class PushTestResponse(BaseModel):
    attempted: int = Field(description="Active subscriptions of the user")
    delivered: int = Field(description="Accepted by the push service")
    errors: list[PushTestFailureResponse]

    @classmethod
    def from_dto(cls, dto: PushTestResultDTO) -> PushTestResponse:
        return cls(
            attempted=dto.attempted,
            delivered=dto.delivered,
            errors=[PushTestFailureResponse(**asdict(error)) for error in dto.errors],
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
