import pytest
from pydantic import ValidationError

from app.config import DEV_SECRET_KEY, Environment, Settings

STRONG_SECRET = "s" * 48


def make(**env: str) -> Settings:
    with pytest.MonkeyPatch.context() as monkeypatch:
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return Settings(_env_file=None)


def test_reads_nested_sections_from_environment() -> None:
    settings = make(
        DATABASE__URL="postgresql+asyncpg://u:p@db:5432/x",
        RATE_LIMIT__AUTH_PER_MINUTE="3",
        AUTH__ACCESS_TOKEN_TTL="PT5M",
    )

    assert settings.database.url.get_secret_value() == "postgresql+asyncpg://u:p@db:5432/x"
    assert settings.rate_limit.auth_per_minute == 3
    assert settings.auth.access_token_ttl.total_seconds() == 300


def test_secrets_are_not_printed() -> None:
    settings = make(AUTH__SECRET_KEY=STRONG_SECRET)

    assert STRONG_SECRET not in repr(settings)
    assert "app:app" not in repr(settings)


def test_prod_requires_a_real_secret() -> None:
    with pytest.raises(ValidationError, match="AUTH__SECRET_KEY"):
        make(ENV="prod")

    settings = make(ENV="prod", AUTH__SECRET_KEY=STRONG_SECRET)
    assert settings.env is Environment.PROD
    assert settings.auth.secret_key.get_secret_value() != DEV_SECRET_KEY


def test_prod_requires_json_logs() -> None:
    with pytest.raises(ValidationError, match="LOG_JSON"):
        make(ENV="prod", AUTH__SECRET_KEY=STRONG_SECRET, LOG_JSON="false")


def test_short_secret_is_rejected_everywhere() -> None:
    with pytest.raises(ValidationError):
        make(AUTH__SECRET_KEY="too-short")
