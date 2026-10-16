import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt

from app.application.interfaces.tokens import (
    AbstractAccessTokenService,
    AbstractRefreshTokenGenerator,
    IssuedAccessToken,
)
from app.domain.common.ids import UserId

_ACCESS_TOKEN_TYPE = "access"  # noqa: S105  # a claim value, not a secret
_REFRESH_TOKEN_BYTES = 32


class JwtAccessTokenService(AbstractAccessTokenService):
    def __init__(self, *, secret_key: str, algorithm: str, ttl: timedelta, issuer: str) -> None:
        self._secret_key = secret_key
        self._algorithm = algorithm
        self._ttl = ttl
        self._issuer = issuer

    def issue(self, user_id: UserId) -> IssuedAccessToken:
        # JWT timestamps have one-second precision.
        now = datetime.now(UTC).replace(microsecond=0)
        expires_at = now + self._ttl
        token = jwt.encode(
            {
                "sub": str(user_id),
                "type": _ACCESS_TOKEN_TYPE,
                "iss": self._issuer,
                "iat": now,
                "exp": expires_at,
            },
            self._secret_key,
            algorithm=self._algorithm,
        )
        return IssuedAccessToken(token=token, expires_at=expires_at)

    def read_user_id(self, token: str) -> UserId | None:
        try:
            payload = jwt.decode(
                token,
                self._secret_key,
                # Never taken from the token header: that would allow "none".
                algorithms=[self._algorithm],
                issuer=self._issuer,
                options={"require": ["sub", "iss", "iat", "exp"]},
            )
            # Other JWTs signed with this key must not pass as access tokens.
            if payload.get("type") != _ACCESS_TOKEN_TYPE:
                return None
            return UserId(UUID(payload["sub"]))
        except jwt.InvalidTokenError, ValueError, TypeError:
            return None


class Sha256RefreshTokenGenerator(AbstractRefreshTokenGenerator):
    """Random 256-bit tokens, stored as SHA-256.

    A fast hash is enough here, unlike for passwords: the tokens are random and
    long, so a leaked hash cannot be brute-forced. It must be deterministic,
    since a presented token is looked up by its hash.
    """

    def generate(self) -> str:
        return secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)

    def hash(self, token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()
