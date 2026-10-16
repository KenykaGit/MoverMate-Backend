from dishka import Provider, Scope, provide

from app.application.interfaces.password_hasher import AbstractPasswordHasher
from app.application.interfaces.tokens import (
    AbstractAccessTokenService,
    AbstractRefreshTokenGenerator,
)
from app.config import AuthSettings, Settings
from app.infrastructure.security.password import Argon2PasswordHasher
from app.infrastructure.security.tokens import JwtAccessTokenService, Sha256RefreshTokenGenerator


class SecurityProvider(Provider):
    scope = Scope.APP

    refresh_token_generator = provide(
        Sha256RefreshTokenGenerator, provides=AbstractRefreshTokenGenerator
    )

    @provide
    def password_hasher(self, config: AuthSettings) -> AbstractPasswordHasher:
        return Argon2PasswordHasher(
            time_cost=config.argon2_time_cost,
            memory_cost_kib=config.argon2_memory_cost_kib,
            parallelism=config.argon2_parallelism,
        )

    @provide
    def access_token_service(
        self, config: AuthSettings, settings: Settings
    ) -> AbstractAccessTokenService:
        return JwtAccessTokenService(
            secret_key=config.secret_key.get_secret_value(),
            algorithm=config.algorithm,
            ttl=config.access_token_ttl,
            issuer=settings.app_name,
        )
