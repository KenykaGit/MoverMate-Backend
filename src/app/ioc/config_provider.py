from dishka import Provider, Scope, from_context, provide

from app.application.auth.dto import RefreshTokenSettings
from app.config import (
    AuthSettings,
    DatabaseSettings,
    IdempotencySettings,
    LlmSettings,
    PushSettings,
    RateLimitSettings,
    RedisSettings,
    Settings,
    WorkerSettings,
)
from app.presentation.http.settings import HttpSettings, RateLimitPolicy


def http_settings_from(settings: Settings) -> HttpSettings:
    return HttpSettings(
        title=settings.app_name,
        version=settings.app_version,
        cors_origins=tuple(settings.cors_origins),
    )


class ConfigProvider(Provider):
    """Splits Settings into the sections and plain settings objects layers need."""

    scope = Scope.APP

    settings = from_context(provides=Settings)

    @provide
    def database(self, settings: Settings) -> DatabaseSettings:
        return settings.database

    @provide
    def redis(self, settings: Settings) -> RedisSettings:
        return settings.redis

    @provide
    def auth(self, settings: Settings) -> AuthSettings:
        return settings.auth

    @provide
    def idempotency(self, settings: Settings) -> IdempotencySettings:
        return settings.idempotency

    @provide
    def push(self, settings: Settings) -> PushSettings:
        return settings.push

    @provide
    def worker(self, settings: Settings) -> WorkerSettings:
        return settings.worker

    @provide
    def llm(self, settings: Settings) -> LlmSettings:
        return settings.llm

    @provide
    def refresh_token_settings(self, auth: AuthSettings) -> RefreshTokenSettings:
        return RefreshTokenSettings(ttl=auth.refresh_token_ttl)

    @provide
    def http_settings(self, settings: Settings) -> HttpSettings:
        return http_settings_from(settings)

    @provide
    def rate_limit_policy(self, settings: Settings) -> RateLimitPolicy:
        limits: RateLimitSettings = settings.rate_limit
        return RateLimitPolicy(
            api_per_window=limits.api_per_minute,
            auth_per_window=limits.auth_per_minute,
            scan_per_window=limits.scan_per_minute,
        )
