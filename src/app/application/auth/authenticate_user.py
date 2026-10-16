from app.application.exceptions import NotAuthenticatedError, UserNotFoundError
from app.application.interfaces.tokens import AbstractAccessTokenService
from app.application.users.dto import UserDTO
from app.application.users.get_user import GetUser


class AuthenticateUser:
    def __init__(self, access_tokens: AbstractAccessTokenService, get_user: GetUser) -> None:
        self._access_tokens = access_tokens
        self._get_user = get_user

    async def __call__(self, access_token: str) -> UserDTO:
        """The user behind an access token.

        Reads the user on every call, so a deleted user is rejected at once,
        even though their access token has not expired yet.

        Raises:
            NotAuthenticatedError: the token is invalid or expired, or its user
                does not exist (anymore).
        """
        user_id = self._access_tokens.read_user_id(access_token)
        if user_id is None:
            raise NotAuthenticatedError
        try:
            return await self._get_user(user_id)
        except UserNotFoundError as error:
            raise NotAuthenticatedError from error
