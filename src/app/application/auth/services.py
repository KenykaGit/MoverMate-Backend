"""Services shared by several auth use cases. They stage changes but never commit."""

import logging
from datetime import datetime

from app.application.auth.dto import RefreshTokenSettings, TokenPairDTO
from app.application.exceptions import InvalidCredentialsError
from app.application.interfaces.password_hasher import AbstractPasswordHasher
from app.application.interfaces.refresh_token_repository import AbstractRefreshTokenRepository
from app.application.interfaces.tokens import (
    AbstractAccessTokenService,
    AbstractRefreshTokenGenerator,
)
from app.application.interfaces.user_repository import AbstractUserRepository
from app.domain.common.errors import DomainError
from app.domain.common.ids import UserId
from app.domain.tokens.entity import RefreshToken, TokenFamilyId
from app.domain.users.entity import User
from app.domain.users.value_objects import Username

logger = logging.getLogger(__name__)


class CredentialsVerifier:
    """Checks a username/password pair and keeps the stored password hash up to date."""

    def __init__(
        self,
        user_repository: AbstractUserRepository,
        password_hasher: AbstractPasswordHasher,
    ) -> None:
        self._user_repository = user_repository
        self._password_hasher = password_hasher

    async def verify(self, username: str, password: str) -> User:
        """The active user with these credentials.

        A hash made with outdated parameters (e.g. after raising the argon2
        cost) is replaced right away: a successful login is the only moment the
        plain password is known. The change is staged; the caller commits.

        Raises:
            InvalidCredentialsError: for an unknown username (a deleted user included)
                and a wrong password alike, in the same time, so the two cannot
                be told apart.
        """
        user = await self._find_user(username)
        if user is None:
            await self._password_hasher.dummy_verify(password)
            # Neither the username nor the password is logged: a mistyped password
            # in the username field would end up in the logs.
            logger.info("login_failed", extra={"reason": "unknown_login"})
            raise InvalidCredentialsError

        if not await self._password_hasher.verify(password, user.password_hash):
            logger.info("login_failed", extra={"reason": "wrong_password"})
            raise InvalidCredentialsError

        if self._password_hasher.needs_rehash(user.password_hash):
            user.change_password(await self._password_hasher.rehash(password))
            await self._user_repository.update(user)
            logger.info("password_rehashed", extra={"user_id": str(user.id)})
        return user

    async def _find_user(self, username: str) -> User | None:
        try:
            return await self._user_repository.get_by_username(Username(username))
        except DomainError:
            return None  # a malformed username cannot belong to anyone


class TokenPairIssuer:
    """Issues an access + refresh token pair within a session (token family)."""

    def __init__(
        self,
        access_tokens: AbstractAccessTokenService,
        refresh_token_generator: AbstractRefreshTokenGenerator,
        refresh_token_repository: AbstractRefreshTokenRepository,
        settings: RefreshTokenSettings,
    ) -> None:
        self._access_tokens = access_tokens
        self._refresh_token_generator = refresh_token_generator
        self._refresh_token_repository = refresh_token_repository
        self._settings = settings

    async def issue(
        self, user_id: UserId, *, family_id: TokenFamilyId, now: datetime
    ) -> TokenPairDTO:
        """Stage the new refresh token; the calling use case commits.

        `family_id` is a new family for a login and the presented token's
        family for a rotation.
        """
        access = self._access_tokens.issue(user_id)
        refresh = self._refresh_token_generator.generate()
        stored = RefreshToken.issue(
            user_id=user_id,
            family_id=family_id,
            token_hash=self._refresh_token_generator.hash(refresh),
            expires_at=now + self._settings.ttl,
        )
        await self._refresh_token_repository.add(stored)

        return TokenPairDTO(
            access_token=access.token,
            access_token_expires_at=access.expires_at,
            refresh_token=refresh,
            refresh_token_expires_at=stored.expires_at,
        )
