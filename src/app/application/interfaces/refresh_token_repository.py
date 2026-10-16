from abc import ABC, abstractmethod
from datetime import datetime

from app.domain.common.ids import UserId
from app.domain.tokens.entity import RefreshToken, RefreshTokenId, TokenFamilyId


class AbstractRefreshTokenRepository(ABC):
    """Persisted refresh tokens (sessions). Implementations must not commit."""

    @abstractmethod
    async def add(self, token: RefreshToken) -> None:
        """Stage a newly issued token."""

    @abstractmethod
    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        """The token with this hash, revoked or expired ones included."""

    @abstractmethod
    async def revoke(self, token_id: RefreshTokenId, now: datetime) -> bool:
        """Revoke the token unless it is already revoked.

        Atomic: of several concurrent calls for one token exactly one returns
        True, so a token can be exchanged only once.
        """

    @abstractmethod
    async def revoke_family(self, family_id: TokenFamilyId, now: datetime) -> int:
        """Revoke every not yet revoked token of one session; return how many."""

    @abstractmethod
    async def revoke_all_for_user(self, user_id: UserId, now: datetime) -> None:
        """Revoke every not yet revoked token of the user (end all sessions)."""
