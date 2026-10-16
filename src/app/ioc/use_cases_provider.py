from dishka import Provider, Scope, provide_all

from app.application.auth.authenticate_user import AuthenticateUser
from app.application.auth.login import Login
from app.application.auth.logout import Logout
from app.application.auth.refresh_tokens import RefreshTokens
from app.application.auth.services import CredentialsVerifier, TokenPairIssuer
from app.application.common.idempotency import IdempotentExecutor
from app.application.users.change_password import ChangePassword
from app.application.users.change_username import ChangeUsername
from app.application.users.get_user import GetUser
from app.application.users.register_user import RegisterUser
from app.application.users.restore_user import RestoreUser
from app.application.users.soft_delete_user import SoftDeleteUser


class UseCaseProvider(Provider):
    """Use cases and application services live per request: they share its
    session and unit of work."""

    scope = Scope.REQUEST

    services = provide_all(CredentialsVerifier, TokenPairIssuer, IdempotentExecutor)

    users = provide_all(
        RegisterUser,
        GetUser,
        ChangePassword,
        ChangeUsername,
        SoftDeleteUser,
        RestoreUser,
    )
    auth = provide_all(Login, RefreshTokens, Logout, AuthenticateUser)
