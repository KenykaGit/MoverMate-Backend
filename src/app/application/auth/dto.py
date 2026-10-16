from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True, kw_only=True)
class LoginDTO:
    login: str
    """Email or username."""
    password: str = field(repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class TokenPairDTO:
    access_token: str = field(repr=False)
    access_token_expires_at: datetime
    refresh_token: str = field(repr=False)
    refresh_token_expires_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class RefreshTokenSettings:
    """Filled from the config by the composition root: the application layer
    must not import the config module itself."""

    ttl: timedelta
