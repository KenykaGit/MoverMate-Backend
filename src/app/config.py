"""Settings from environment variables and `.env`.

Nested sections use `__` as a delimiter: `DATABASE__URL`, `AUTH__SECRET_KEY`,
`RATE_LIMIT__AUTH_PER_MINUTE`... Only the composition root (app.ioc, app.main)
and migrations read settings; every other layer receives plain values.
"""

from datetime import timedelta
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# HS256 needs a key at least as long as its 256-bit output (RFC 7518 §3.2).
MIN_SECRET_KEY_LENGTH = 32
DEV_SECRET_KEY = "dev-only-insecure-secret-key-change-me!!"  # noqa: S105  # rejected in prod

_SETTINGS_CONFIG = SettingsConfigDict(
    env_file=".env",
    env_nested_delimiter="__",
    extra="ignore",
)


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    PROD = "prod"


class DatabaseSettings(BaseModel):
    url: SecretStr = SecretStr("postgresql+asyncpg://app:app@localhost:5432/app")
    pool_size: int = Field(default=10, ge=1)
    echo: bool = False


class RedisSettings(BaseModel):
    url: SecretStr = SecretStr("redis://localhost:6379/0")
    # Short: a sick Redis must degrade features, not request latency.
    timeout_seconds: float = Field(default=0.2, gt=0)


class AuthSettings(BaseModel):
    secret_key: SecretStr = Field(
        default=SecretStr(DEV_SECRET_KEY), min_length=MIN_SECRET_KEY_LENGTH
    )
    algorithm: str = "HS256"
    access_token_ttl: timedelta = timedelta(minutes=15)
    refresh_token_ttl: timedelta = timedelta(days=30)
    # argon2-cffi defaults (RFC 9106 low-memory profile): ~50 ms and 64 MiB per hash.
    argon2_time_cost: int = Field(default=3, ge=1)
    argon2_memory_cost_kib: int = Field(default=64 * 1024, ge=8)
    argon2_parallelism: int = Field(default=4, ge=1)


class RateLimitSettings(BaseModel):
    """Requests per minute per client IP; 0 disables a scope."""

    api_per_minute: int = Field(default=120, ge=0)
    auth_per_minute: int = Field(default=10, ge=0)


class IdempotencySettings(BaseModel):
    pending_ttl_seconds: int = Field(default=30, ge=1)
    completed_ttl_seconds: int = Field(default=24 * 60 * 60, ge=1)


class Settings(BaseSettings):
    model_config = _SETTINGS_CONFIG

    app_name: str = "reference-backend"
    app_version: str = "dev"
    """Injected at image build time (git sha); shown by /health/ready."""
    env: Environment = Environment.LOCAL
    log_level: str = "INFO"
    log_json: bool = True
    cors_origins: list[str] = []

    database: DatabaseSettings = DatabaseSettings()
    redis: RedisSettings = RedisSettings()
    auth: AuthSettings = AuthSettings()
    rate_limit: RateLimitSettings = RateLimitSettings()
    idempotency: IdempotencySettings = IdempotencySettings()

    @model_validator(mode="after")
    def _check_prod_secrets(self) -> Self:
        if self.env is Environment.PROD:
            if self.auth.secret_key.get_secret_value() == DEV_SECRET_KEY:
                raise ValueError("AUTH__SECRET_KEY must be set to a random value in prod")
            if self.log_json is False:
                raise ValueError("LOG_JSON must be true in prod: logs are parsed by machines")
        return self


class MigrationSettings(BaseSettings):
    """The part of Settings that Alembic needs: migrations must not require secrets."""

    model_config = _SETTINGS_CONFIG

    database: DatabaseSettings = DatabaseSettings()
