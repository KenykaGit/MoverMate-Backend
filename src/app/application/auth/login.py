import logging

from app.application.auth.dto import LoginDTO, TokenPairDTO
from app.application.auth.services import CredentialsVerifier, TokenPairIssuer
from app.application.common.clock import utc_now
from app.application.common.uow import AbstractUnitOfWork
from app.domain.tokens.entity import new_token_family

logger = logging.getLogger(__name__)


class Login:
    def __init__(
        self,
        credentials_verifier: CredentialsVerifier,
        token_pair_issuer: TokenPairIssuer,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._credentials_verifier = credentials_verifier
        self._token_pair_issuer = token_pair_issuer
        self._uow = uow

    async def __call__(self, data: LoginDTO) -> TokenPairDTO:
        """Check credentials and start a new session (a new token family).

        Also commits a password hash upgraded by the credentials check.

        Raises:
            InvalidCredentialsError: see CredentialsVerifier.verify.
        """
        user = await self._credentials_verifier.verify(data.login, data.password)
        tokens = await self._token_pair_issuer.issue(
            user.id, family_id=new_token_family(), now=utc_now()
        )
        await self._uow.commit()

        logger.info("user_logged_in", extra={"user_id": str(user.id)})
        return tokens
