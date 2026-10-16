import logging

from app.application.common.clock import utc_now
from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.refresh_token_repository import AbstractRefreshTokenRepository
from app.application.interfaces.tokens import AbstractRefreshTokenGenerator

logger = logging.getLogger(__name__)


class Logout:
    def __init__(
        self,
        refresh_token_repository: AbstractRefreshTokenRepository,
        refresh_token_generator: AbstractRefreshTokenGenerator,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._refresh_token_repository = refresh_token_repository
        self._refresh_token_generator = refresh_token_generator
        self._uow = uow

    async def __call__(self, refresh_token: str) -> None:
        """End the session (token family) of this refresh token.

        Any token of the session ends it, an already rotated one too: the
        client may hold a stale token after a lost refresh response.
        Idempotent and silent: an unknown token or an ended session is not an
        error, so the endpoint cannot be used to probe tokens. Issued access
        tokens stay valid until they expire (15 minutes by default).
        """
        token = await self._refresh_token_repository.get_by_hash(
            self._refresh_token_generator.hash(refresh_token)
        )
        if token is None:
            return
        if await self._refresh_token_repository.revoke_family(token.family_id, utc_now()):
            await self._uow.commit()
            logger.info(
                "user_logged_out",
                extra={"user_id": str(token.user_id), "family_id": str(token.family_id)},
            )
