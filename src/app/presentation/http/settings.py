from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True, kw_only=True)
class HttpSettings:
    """What the HTTP layer needs from the config (filled by the composition root)."""

    title: str
    version: str
    cors_origins: tuple[str, ...] = ()
    seed_test_users: bool = False
    seed_test_password: str | None = field(default=None, repr=False)
    """None: the default password of the seed catalog."""


@dataclass(frozen=True, slots=True, kw_only=True)
class RateLimitPolicy:
    """Requests per window per client IP; 0 disables a scope."""

    api_per_window: int
    auth_per_window: int
    scan_per_window: int
    window_seconds: int = 60
