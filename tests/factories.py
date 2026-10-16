"""Builders of domain objects with sensible defaults for tests."""

from datetime import UTC, datetime, timedelta

from app.domain.common.ids import UserId, new_id
from app.domain.tokens.entity import RefreshToken, TokenFamilyId, new_token_family
from app.domain.users.entity import User
from app.domain.users.value_objects import Email, PasswordHash, Username

PASSWORD_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaGhhc2hoYXNo"


def make_user(
    *,
    email: str = "john@example.com",
    username: str = "john_doe",
    password_hash: str = PASSWORD_HASH,
    is_active: bool = True,
) -> User:
    user = User.register(
        email=Email(email), username=Username(username), password_hash=PasswordHash(password_hash)
    )
    if not is_active:
        user.soft_delete()
    return user


def user_state(user: User) -> tuple[object, ...]:
    """All attributes of a user (User equality compares ids only)."""
    return (user.id, user.email, user.username, user.password_hash, user.is_active)


def make_refresh_token(
    user_id: UserId,
    *,
    family_id: TokenFamilyId | None = None,
    token_hash: str | None = None,
    expires_in: timedelta = timedelta(days=1),
    revoked: bool = False,
) -> RefreshToken:
    """A token of `family_id`, or the first token of a new session."""
    now = datetime.now(UTC)
    token = RefreshToken.issue(
        user_id=user_id,
        family_id=family_id or new_token_family(),
        token_hash=token_hash or new_id().hex + new_id().hex,  # 64 hex chars
        expires_at=now + expires_in,
    )
    if revoked:
        token.revoke(now)
    return token
