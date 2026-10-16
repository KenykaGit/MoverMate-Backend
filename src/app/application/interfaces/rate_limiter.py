from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int
    """Seconds until the current window resets."""


class AbstractRateLimiter(ABC):
    @abstractmethod
    async def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision | None:
        """Count one request against `key`.

        None when the limiter's storage is unavailable: callers fail open and
        let the request through.
        """
