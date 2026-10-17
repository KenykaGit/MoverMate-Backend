import re
from dataclasses import dataclass, field
from typing import NewType, Self
from uuid import UUID

from app.domain.common.entity import Entity
from app.domain.common.ids import UserId, new_id
from app.domain.notifications.errors import InvalidPushSubscriptionError

PushSubscriptionId = NewType("PushSubscriptionId", UUID)

ENDPOINT_MAX_LENGTH = 2048
KEY_MAX_LENGTH = 256
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+={0,2}")


@dataclass(frozen=True, slots=True)
class PushEndpoint:
    """The push service URL of one browser installation; unique across users."""

    value: str

    def __post_init__(self) -> None:
        value = self.value.strip()
        if not value.startswith("https://") or len(value) > ENDPOINT_MAX_LENGTH:
            raise InvalidPushSubscriptionError(
                f"Push endpoint must be an https URL of at most {ENDPOINT_MAX_LENGTH} characters"
            )
        object.__setattr__(self, "value", value)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class PushKeys:
    """The browser's keys that encrypt a push message (base64url)."""

    p256dh: str = field(repr=False)
    auth: str = field(repr=False)

    def __post_init__(self) -> None:
        for key in (self.p256dh, self.auth):
            if len(key) > KEY_MAX_LENGTH or not _BASE64URL.fullmatch(key):
                raise InvalidPushSubscriptionError("Push subscription keys must be base64url")


@dataclass(eq=False, kw_only=True, slots=True)
class PushSubscription(Entity[PushSubscriptionId]):
    """Where to deliver Web Push messages for a user: one per browser."""

    user_id: UserId
    endpoint: PushEndpoint
    keys: PushKeys
    is_active: bool
    """False once the push service reports the endpoint gone (404 or 410)."""

    @classmethod
    def subscribe(cls, *, user_id: UserId, endpoint: PushEndpoint, keys: PushKeys) -> Self:
        return cls(
            id=PushSubscriptionId(new_id()),
            user_id=user_id,
            endpoint=endpoint,
            keys=keys,
            is_active=True,
        )

    def renew(self, *, user_id: UserId, keys: PushKeys) -> None:
        """The browser subscribed again, possibly under another user: it is theirs now."""
        self.user_id = user_id
        self.keys = keys
        self.is_active = True

    def deactivate(self) -> None:
        """Idempotent."""
        self.is_active = False
