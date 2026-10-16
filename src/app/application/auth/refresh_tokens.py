import logging

from app.application.auth.dto import TokenPairDTO
from app.application.auth.services import TokenPairIssuer
from app.application.common.clock import utc_now
from app.application.common.uow import AbstractUnitOfWork
from app.application.exceptions import InvalidRefreshTokenError
from app.application.interfaces.refresh_token_repository import AbstractRefreshTokenRepository
from app.application.interfaces.tokens import AbstractRefreshTokenGenerator
from app.application.interfaces.user_repository import AbstractUserRepository

logger = logging.getLogger(__name__)


class RefreshTokens:
    def __init__(
        self,
        refresh_token_repository: AbstractRefreshTokenRepository,
        refresh_token_generator: AbstractRefreshTokenGenerator,
        user_repository: AbstractUserRepository,
        token_pair_issuer: TokenPairIssuer,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._refresh_token_repository = refresh_token_repository
        self._refresh_token_generator = refresh_token_generator
        self._user_repository = user_repository
        self._token_pair_issuer = token_pair_issuer
        self._uow = uow

    async def __call__(self, refresh_token: str) -> TokenPairDTO:
        """Exchange a refresh token for a new pair in the same session (rotation).

        Each refresh token works once. Presenting an already used one means it
        leaked (the legitimate client already got a new one), so its whole
        family is revoked: the thief and the victim of that session both have
        to log in again. Other sessions of the user (devices) keep working.

        Raises:
            InvalidRefreshTokenError: unknown, expired or reused token, or the
                user has been deleted.
        """
        now = utc_now()
        token = await self._refresh_token_repository.get_by_hash(
            self._refresh_token_generator.hash(refresh_token)
        )
        if token is None or token.is_expired(now):
            raise InvalidRefreshTokenError

        # revoke() is an atomic conditional UPDATE: of two concurrent refreshes
        # with one token exactly one wins, the other is treated as reuse.
        if token.is_revoked or not await self._refresh_token_repository.revoke(token.id, now):
            await self._refresh_token_repository.revoke_family(token.family_id, now)
            await self._uow.commit()
            logger.warning(
                "refresh_token_reused",
                extra={
                    "user_id": str(token.user_id),
                    "family_id": str(token.family_id),
                    "token_id": str(token.id),
                },
            )
            raise InvalidRefreshTokenError

        if await self._user_repository.get_by_id(token.user_id) is None:
            await self._uow.commit()  # keep the token revoked
            raise InvalidRefreshTokenError

        tokens = await self._token_pair_issuer.issue(
            token.user_id, family_id=token.family_id, now=now
        )
        await self._uow.commit()
        return tokens
