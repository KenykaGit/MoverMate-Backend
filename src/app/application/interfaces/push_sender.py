from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum

from app.domain.notifications.push_subscription import PushSubscription


@dataclass(frozen=True, slots=True, kw_only=True)
class PushMessage:
    """What the service worker of the browser shows. Built at sending time from
    current data; never contains the contents of boxes."""

    title: str
    body: str
    url: str
    """A path of the frontend to open on click."""
    data: dict[str, str] = field(default_factory=dict)
    """Ids for the client: type, project_id, notification_id..."""


class PushOutcome(StrEnum):
    DELIVERED = "DELIVERED"
    """The push service accepted the message."""
    GONE = "GONE"
    """The subscription no longer exists (404/410): never use it again."""
    FAILED = "FAILED"
    """Anything else (timeout, 5xx, 429...): worth retrying later."""


@dataclass(frozen=True, slots=True)
class PushResult:
    outcome: PushOutcome
    detail: str | None = None
    """Why it failed, for logs and Notification.last_error."""


class AbstractPushSender(ABC):
    @abstractmethod
    async def send(self, subscription: PushSubscription, message: PushMessage) -> PushResult:
        """Encrypt and hand the message to the push service of the subscription.

        Never raises for delivery problems: they are reported as the result.
        """
