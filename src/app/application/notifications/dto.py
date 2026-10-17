from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID


class DeliveryOutcome(StrEnum):
    """What one delivery attempt did to a due notification."""

    SENT = "SENT"
    """At least one browser of the recipient got it."""
    SKIPPED = "SKIPPED"
    """The recipient has no working subscription."""
    CANCELLED = "CANCELLED"
    """Its reason is gone: the task is done, the box packed, the project deleted..."""
    RETRY = "RETRY"
    """Every push service failed; it is due again after a backoff delay."""
    FAILED = "FAILED"
    """Failed for the last allowed time: given up."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SubscribeDTO:
    """What `PushSubscription.toJSON()` of the browser contains."""

    user_id: UUID
    endpoint: str = field(repr=False)
    """A capability: anyone who knows it can push to the browser."""
    p256dh: str = field(repr=False)
    auth: str = field(repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class UnsubscribeDTO:
    user_id: UUID
    endpoint: str = field(repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class SendTestPushDTO:
    username: str
    text: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PushTestFailureDTO:
    """One subscription the test message did not reach. The endpoint is a
    capability, so only the subscription id is reported."""

    subscription_id: UUID
    outcome: str
    """`GONE` (the subscription was deactivated) or `FAILED`."""
    detail: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class PushTestResultDTO:
    attempted: int
    delivered: int
    errors: list[PushTestFailureDTO]
