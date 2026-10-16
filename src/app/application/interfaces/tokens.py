from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.common.ids import UserId


@dataclass(frozen=True, slots=True, kw_only=True)
class IssuedAccessToken:
    token: str = field(repr=False)
    expires_at: datetime


class AbstractAccessTokenService(ABC):
    """Short-lived, self-contained tokens proving who the caller is."""

    @abstractmethod
    def issue(self, user_id: UserId) -> IssuedAccessToken:
        """An access token for the user."""

    @abstractmethod
    def read_user_id(self, token: str) -> UserId | None:
        """The user the token was issued for.

        None for any token that is not a valid, unexpired access token:
        callers must not care why it was rejected.
        """


class AbstractRefreshTokenGenerator(ABC):
    @abstractmethod
    def generate(self) -> str:
        """A new unguessable refresh token."""

    @abstractmethod
    def hash(self, token: str) -> str:
        """The deterministic hash under which a token is stored and looked up."""
