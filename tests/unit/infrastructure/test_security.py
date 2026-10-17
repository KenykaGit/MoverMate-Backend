from datetime import timedelta

import jwt
import pytest

from app.domain.common.ids import UserId, new_id
from app.domain.users.value_objects import PasswordHash, RawPassword
from app.infrastructure.security.password import Argon2PasswordHasher
from app.infrastructure.security.tokens import JwtAccessTokenService, Sha256RefreshTokenGenerator

SECRET = "test-secret-key-that-is-long-enough-for-hs256"
USER_ID = UserId(new_id())


@pytest.fixture(scope="module")
def hasher() -> Argon2PasswordHasher:
    # Cheap parameters: the real ones cost ~50 ms and 64 MiB per hash.
    return Argon2PasswordHasher(time_cost=1, memory_cost_kib=8, parallelism=1)


@pytest.fixture
def tokens() -> JwtAccessTokenService:
    return JwtAccessTokenService(
        secret_key=SECRET, algorithm="HS256", ttl=timedelta(minutes=15), issuer="tests"
    )


class TestArgon2:
    async def test_hash_verifies_only_the_right_password(
        self, hasher: Argon2PasswordHasher
    ) -> None:
        password_hash = await hasher.hash(RawPassword("correct horse"))

        assert password_hash.value.startswith("$argon2id$")
        assert await hasher.verify("correct horse", password_hash)
        assert not await hasher.verify("wrong horse", password_hash)

    async def test_salted(self, hasher: Argon2PasswordHasher) -> None:
        first = await hasher.hash(RawPassword("correct horse"))
        second = await hasher.hash(RawPassword("correct horse"))

        assert first != second

    async def test_malformed_hash_is_a_mismatch(self, hasher: Argon2PasswordHasher) -> None:
        assert not await hasher.verify("anything", PasswordHash("not-an-argon2-hash"))

    async def test_dummy_verify_does_not_raise(self, hasher: Argon2PasswordHasher) -> None:
        await hasher.dummy_verify("anything")

    async def test_needs_rehash_only_after_the_parameters_change(
        self, hasher: Argon2PasswordHasher
    ) -> None:
        stronger = Argon2PasswordHasher(time_cost=2, memory_cost_kib=16, parallelism=1)
        old_hash = await hasher.hash(RawPassword("correct horse"))

        assert not hasher.needs_rehash(old_hash)
        assert stronger.needs_rehash(old_hash)
        assert await stronger.verify("correct horse", old_hash), "old hashes still verify"

        new_hash = await stronger.rehash("short")  # no policy check: already verified
        assert not stronger.needs_rehash(new_hash)
        assert await stronger.verify("short", new_hash)

    def test_malformed_hash_needs_no_rehash(self, hasher: Argon2PasswordHasher) -> None:
        assert not hasher.needs_rehash(PasswordHash("not-an-argon2-hash"))


class TestJwtAccessTokens:
    def test_roundtrip(self, tokens: JwtAccessTokenService) -> None:
        issued = tokens.issue(USER_ID)

        assert tokens.read_user_id(issued.token) == USER_ID

    def test_expired(self) -> None:
        expired = JwtAccessTokenService(
            secret_key=SECRET, algorithm="HS256", ttl=-timedelta(seconds=1), issuer="tests"
        )

        assert expired.read_user_id(expired.issue(USER_ID).token) is None

    @pytest.mark.parametrize(
        "claims",
        [
            {"type": "refresh"},  # another kind of token signed with the same key
            {"iss": "someone-else"},
            {"sub": "not-a-uuid"},
        ],
    )
    def test_rejects_foreign_claims(
        self, tokens: JwtAccessTokenService, claims: dict[str, str]
    ) -> None:
        payload = (
            jwt.decode(
                tokens.issue(USER_ID).token,
                SECRET,
                algorithms=["HS256"],
                options={"verify_signature": False},
            )
            | claims
        )
        forged = jwt.encode(payload, SECRET, algorithm="HS256")

        assert tokens.read_user_id(forged) is None

    def test_rejects_other_keys_and_the_none_algorithm(self, tokens: JwtAccessTokenService) -> None:
        payload = jwt.decode(tokens.issue(USER_ID).token, options={"verify_signature": False})

        assert tokens.read_user_id(jwt.encode(payload, "x" * 40, algorithm="HS256")) is None
        assert tokens.read_user_id(jwt.encode(payload, None, algorithm="none")) is None
        assert tokens.read_user_id("garbage") is None


class TestRefreshTokenGenerator:
    def test_tokens_are_random_and_hashes_deterministic(self) -> None:
        generator = Sha256RefreshTokenGenerator()
        first, second = generator.generate(), generator.generate()

        assert first != second
        assert len(first) >= 43  # 32 random bytes, url-safe base64
        assert generator.hash(first) == generator.hash(first)
        assert len(generator.hash(first)) == 64
        assert generator.hash(first) != generator.hash(second)
