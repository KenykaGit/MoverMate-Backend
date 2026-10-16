from datetime import timedelta

import pytest

from app.application.auth.dto import RefreshTokenSettings
from app.application.auth.services import CredentialsVerifier, TokenPairIssuer
from tests.fakes import (
    FakeAccessTokenService,
    FakePasswordHasher,
    FakeRefreshTokenGenerator,
    FakeUnitOfWork,
    InMemoryRefreshTokenRepository,
    InMemoryUserRepository,
)

REFRESH_TOKEN_TTL_DAYS = 30


@pytest.fixture
def users() -> InMemoryUserRepository:
    return InMemoryUserRepository()


@pytest.fixture
def refresh_tokens() -> InMemoryRefreshTokenRepository:
    return InMemoryRefreshTokenRepository()


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def hasher() -> FakePasswordHasher:
    return FakePasswordHasher()


@pytest.fixture
def access_tokens() -> FakeAccessTokenService:
    return FakeAccessTokenService()


@pytest.fixture
def refresh_token_generator() -> FakeRefreshTokenGenerator:
    return FakeRefreshTokenGenerator()


@pytest.fixture
def credentials_verifier(
    users: InMemoryUserRepository, hasher: FakePasswordHasher
) -> CredentialsVerifier:
    return CredentialsVerifier(users, hasher)


@pytest.fixture
def token_pair_issuer(
    access_tokens: FakeAccessTokenService,
    refresh_token_generator: FakeRefreshTokenGenerator,
    refresh_tokens: InMemoryRefreshTokenRepository,
) -> TokenPairIssuer:
    return TokenPairIssuer(
        access_tokens,
        refresh_token_generator,
        refresh_tokens,
        RefreshTokenSettings(ttl=timedelta(days=REFRESH_TOKEN_TTL_DAYS)),
    )
