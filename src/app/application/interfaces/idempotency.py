from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None


class IdempotencyState(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True, kw_only=True)
class IdempotencyRecord:
    state: IdempotencyState
    fingerprint: str
    """Hash of the request payload: the same key must come with the same payload."""
    response: JsonValue = None
    """The stored result; set for COMPLETED records only."""


class BeginOutcome(StrEnum):
    STARTED = "started"
    """The key was free and is now PENDING: the caller runs the operation."""
    EXISTS = "exists"
    """The key is already taken: see BeginResult.record."""
    UNAVAILABLE = "unavailable"
    """The store failed: proceed without idempotency (fail-open)."""


@dataclass(frozen=True, slots=True)
class BeginResult:
    outcome: BeginOutcome
    record: IdempotencyRecord | None = None


class AbstractIdempotencyStore(ABC):
    """Remembers results of operations by client-provided keys.

    Fail-open: implementations never raise because the store is down. They
    report UNAVAILABLE from begin() and silently skip complete()/release().
    """

    @abstractmethod
    async def begin(self, key: str, fingerprint: str) -> BeginResult:
        """Atomically claim the key as PENDING (short TTL) unless it exists."""

    @abstractmethod
    async def complete(self, key: str, fingerprint: str, response: JsonValue) -> None:
        """Mark the key COMPLETED with the result (long TTL)."""

    @abstractmethod
    async def release(self, key: str) -> None:
        """Forget the key, so the client may retry after a failure."""
