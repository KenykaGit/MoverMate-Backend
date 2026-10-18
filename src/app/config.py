"""Settings from environment variables and `.env`.

Nested sections use `__` as a delimiter: `DATABASE__URL`, `AUTH__SECRET_KEY`,
`RATE_LIMIT__AUTH_PER_MINUTE`... Only the composition root (app.ioc, app.main)
and migrations read settings; every other layer receives plain values.
"""

from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
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
    scan_per_minute: int = Field(default=10, ge=0)
    """Photo scans: every one is a paid call to the LLM."""


class IdempotencySettings(BaseModel):
    pending_ttl_seconds: int = Field(default=30, ge=1)
    completed_ttl_seconds: int = Field(default=24 * 60 * 60, ge=1)


class PushSettings(BaseModel):
    """Web Push with VAPID (RFC 8292): how the worker signs and sends messages."""

    vapid_private_key: SecretStr | None = None
    """PEM of a P-256 key. Unset: the key is read from `vapid_key_file`, and
    generated there on first use, so that no key has to be issued by hand."""
    vapid_key_file: Path = Path(".vapid/private_key.pem")
    vapid_subject: str = Field(
        default="mailto:admin@example.com", pattern=r"^(mailto:|https://)\S+$"
    )
    """Contact for push services (the `sub` claim): a mailto: or https:// URL."""
    ttl_seconds: int = Field(default=24 * 60 * 60, ge=0)
    """How long a push service keeps a message for an offline device."""
    timeout_seconds: float = Field(default=10.0, gt=0)


class WorkerSettings(BaseModel):
    """The notification worker on taskiq (app.worker): scheduler, Redis queue, worker."""

    interval_seconds: int = Field(default=15, ge=1)
    """How often the scheduler wakes the worker up (taskiq `interval` schedule)."""
    queue_name: str = "taskiq:notifications"
    """The Redis list of the broker (in the REDIS__URL database)."""
    batch_size: int = Field(default=100, ge=1)
    """Notifications sent per wake-up at most; the rest waits for the next one."""
    heartbeat_file: Path = Path("/tmp/worker-heartbeat")  # noqa: S108  # container-local
    """Touched on start and after every successful run; the healthcheck reads its age."""
    max_lag_seconds: int = Field(default=5 * 60, ge=1)
    """/health/ready reports `degraded` when a due notification waits longer."""


class LlmSettings(BaseModel):
    """An OpenAI-compatible Chat Completions API: the Spirit LLM proxy by default.

    Used by photo scanning only: without `api_key` the scan endpoint answers 503
    `item_recognition_disabled` and everything else works as usual.
    """

    base_url: str = Field(
        default="https://llm-proxy.spirit.t-education.com/v1", pattern=r"^https?://\S+$"
    )
    api_key: SecretStr | None = None
    scan_model: str = Field(default="openrouter/claude-haiku-5-5", min_length=1)
    """`<provider>/<model>` as the proxy names it."""
    timeout_seconds: float = Field(default=30.0, gt=0)

    @field_validator("api_key", mode="before")
    @classmethod
    def _empty_key_is_none(cls, value: object) -> object:
        # Compose passes unset variables as "": that means "disabled", not a key.
        if value == "" or (isinstance(value, SecretStr) and value.get_secret_value() == ""):
            return None
        return value


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
    push: PushSettings = PushSettings()
    worker: WorkerSettings = WorkerSettings()
    llm: LlmSettings = LlmSettings()

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
