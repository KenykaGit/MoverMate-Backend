from datetime import UTC, datetime, timedelta

from app.domain.common.ids import UserId, new_id
from app.domain.tokens.entity import RefreshToken, new_token_family

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def make_token(expires_at: datetime = NOW + timedelta(days=1)) -> RefreshToken:
    return RefreshToken.issue(
        user_id=UserId(new_id()),
        family_id=new_token_family(),
        token_hash="h" * 64,
        expires_at=expires_at,
    )


def test_issued_token_is_active() -> None:
    token = make_token()

    assert not token.is_revoked
    assert not token.is_expired(NOW)


def test_expires_at_its_expiry_time() -> None:
    token = make_token(expires_at=NOW)

    assert token.is_expired(NOW)
    assert not token.is_expired(NOW - timedelta(seconds=1))


def test_revoke_keeps_the_first_revocation_time() -> None:
    token = make_token()

    token.revoke(NOW)
    token.revoke(NOW + timedelta(hours=1))

    assert token.is_revoked
    assert token.revoked_at == NOW


def test_every_login_starts_its_own_family() -> None:
    assert new_token_family() != new_token_family()
