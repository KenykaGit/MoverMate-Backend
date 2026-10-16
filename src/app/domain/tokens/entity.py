from dataclasses import dataclass
from datetime import datetime
from typing import NewType, Self
from uuid import UUID

from app.domain.common.entity import Entity
from app.domain.common.ids import UserId, new_id

RefreshTokenId = NewType("RefreshTokenId", UUID)
TokenFamilyId = NewType("TokenFamilyId", UUID)
"""One session: the tokens of a login and all their rotations share it."""


def new_token_family() -> TokenFamilyId:
    """The family of a new session (a login); rotations keep their family."""
    return TokenFamilyId(new_id())


@dataclass(eq=False, kw_only=True, slots=True)
class RefreshToken(Entity[RefreshTokenId]):
    """A long-lived credential exchanged for new access tokens.

    Only a hash of the token is kept: the token itself is shown to the
    client once and never stored, so a database leak cannot be replayed.
    Each rotation issues a new token in the same family; replaying a revoked
    token ends the whole family, i.e. that session, and no other.
    """

    user_id: UserId
    family_id: TokenFamilyId
    token_hash: str
    expires_at: datetime
    revoked_at: datetime | None

    @classmethod
    def issue(
        cls,
        *,
        user_id: UserId,
        family_id: TokenFamilyId,
        token_hash: str,
        expires_at: datetime,
    ) -> Self:
        return cls(
            id=RefreshTokenId(new_id()),
            user_id=user_id,
            family_id=family_id,
            token_hash=token_hash,
            expires_at=expires_at,
            revoked_at=None,
        )

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def revoke(self, now: datetime) -> None:
        """Idempotent: the first revocation time is kept."""
        if self.revoked_at is None:
            self.revoked_at = now
