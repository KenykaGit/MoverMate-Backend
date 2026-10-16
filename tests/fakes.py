"""In-memory fakes of the application ports: fast, deterministic, inspectable.

Repositories store and return copies, like a real database: a use case that
mutates a loaded entity without calling update() does not "persist" anything,
and the tests catch that.
"""

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.application.common.uow import AbstractUnitOfWork
from app.application.exceptions import (
    UsernameAlreadyTakenError,
    UserNotFoundError,
)
from app.application.interfaces.health import AbstractHealthProbe
from app.application.interfaces.idempotency import (
    AbstractIdempotencyStore,
    BeginOutcome,
    BeginResult,
    IdempotencyRecord,
    IdempotencyState,
    JsonValue,
)
from app.application.interfaces.password_hasher import AbstractPasswordHasher
from app.application.interfaces.rate_limiter import AbstractRateLimiter, RateLimitDecision
from app.application.interfaces.refresh_token_repository import AbstractRefreshTokenRepository
from app.application.interfaces.tokens import (
    AbstractAccessTokenService,
    AbstractRefreshTokenGenerator,
    IssuedAccessToken,
)
from app.application.interfaces.user_repository import AbstractUserRepository
from app.domain.common.ids import UserId
from app.domain.tokens.entity import RefreshToken, RefreshTokenId, TokenFamilyId
from app.domain.users.entity import User
from app.domain.users.value_objects import PasswordHash, RawPassword, Username


class InMemoryUserRepository(AbstractUserRepository):
    """`users` holds every user, soft-deleted ones included."""

    def __init__(self, users: Iterable[User] = ()) -> None:
        self.users: dict[UserId, User] = {user.id: replace(user) for user in users}

    async def add(self, user: User) -> None:
        self._check_unique(user)
        self.users[user.id] = replace(user)

    async def get_by_id(self, user_id: UserId, *, include_deleted: bool = False) -> User | None:
        user = self.users.get(user_id)
        if user is None or not (user.is_active or include_deleted):
            return None
        return replace(user)

    async def get_by_username(self, username: Username) -> User | None:
        return self._first_active(lambda user: user.username == username)

    async def update(self, user: User) -> None:
        if user.id not in self.users:
            raise UserNotFoundError(user.id)
        self._check_unique(user)
        self.users[user.id] = replace(user)

    def _active(self) -> list[User]:
        return [user for user in self.users.values() if user.is_active]

    def _first_active(self, predicate: Callable[[User], bool]) -> User | None:
        user = next((user for user in self._active() if predicate(user)), None)
        return None if user is None else replace(user)

    def _check_unique(self, user: User) -> None:
        """Mirrors the partial unique indexes: only active users clash."""
        if not user.is_active:
            return
        others = [other for other in self._active() if other.id != user.id]
        if any(other.username == user.username for other in others):
            raise UsernameAlreadyTakenError(user.username)


class InMemoryRefreshTokenRepository(AbstractRefreshTokenRepository):
    def __init__(self, tokens: Iterable[RefreshToken] = ()) -> None:
        self.tokens: dict[RefreshTokenId, RefreshToken] = {t.id: replace(t) for t in tokens}

    async def add(self, token: RefreshToken) -> None:
        self.tokens[token.id] = replace(token)

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        token = next((t for t in self.tokens.values() if t.token_hash == token_hash), None)
        return None if token is None else replace(token)

    async def revoke(self, token_id: RefreshTokenId, now: datetime) -> bool:
        token = self.tokens.get(token_id)
        if token is None or token.is_revoked:
            return False
        token.revoke(now)
        return True

    async def revoke_family(self, family_id: TokenFamilyId, now: datetime) -> int:
        family = [t for t in self.tokens.values() if t.family_id == family_id and not t.is_revoked]
        for token in family:
            token.revoke(now)
        return len(family)

    async def revoke_all_for_user(self, user_id: UserId, now: datetime) -> None:
        for token in self.tokens.values():
            if token.user_id == user_id:
                token.revoke(now)

    def active_for(self, user_id: UserId) -> list[RefreshToken]:
        return [t for t in self.tokens.values() if t.user_id == user_id and not t.is_revoked]


class FakeUnitOfWork(AbstractUnitOfWork):
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakePasswordHasher(AbstractPasswordHasher):
    """Deterministic and fast; counts calls to the expensive operations.

    Hashes "made with outdated parameters" carry another prefix: they verify,
    but needs_rehash() asks for an upgrade.
    """

    PREFIX = "fake-hash:"
    OUTDATED_PREFIX = "fake-hash-outdated:"

    def __init__(self) -> None:
        self.hash_calls = 0
        self.verify_calls = 0
        self.dummy_verify_calls = 0

    @classmethod
    def hash_of(cls, password: str, *, outdated: bool = False) -> PasswordHash:
        """The hash this fake produces: seeds users with known passwords."""
        return PasswordHash((cls.OUTDATED_PREFIX if outdated else cls.PREFIX) + password[::-1])

    async def hash(self, password: RawPassword) -> PasswordHash:
        return await self.rehash(password.value)

    async def rehash(self, password: str) -> PasswordHash:
        self.hash_calls += 1
        return self.hash_of(password)

    async def verify(self, password: str, password_hash: PasswordHash) -> bool:
        self.verify_calls += 1
        return password_hash in {self.hash_of(password), self.hash_of(password, outdated=True)}

    async def dummy_verify(self, password: str) -> None:  # noqa: ARG002
        self.dummy_verify_calls += 1

    def needs_rehash(self, password_hash: PasswordHash) -> bool:
        return password_hash.value.startswith(self.OUTDATED_PREFIX)


class FakeAccessTokenService(AbstractAccessTokenService):
    """Readable tokens `access:<user id>`."""

    PREFIX = "access:"
    TTL = timedelta(minutes=15)

    def issue(self, user_id: UserId) -> IssuedAccessToken:
        return IssuedAccessToken(
            token=f"{self.PREFIX}{user_id}", expires_at=datetime.now(UTC) + self.TTL
        )

    def read_user_id(self, token: str) -> UserId | None:
        if not token.startswith(self.PREFIX):
            return None
        try:
            return UserId(UUID(token.removeprefix(self.PREFIX)))
        except ValueError:
            return None


class FakeRefreshTokenGenerator(AbstractRefreshTokenGenerator):
    """Sequential tokens `refresh-1`, `refresh-2`... with a readable hash."""

    def __init__(self) -> None:
        self._issued = 0

    def generate(self) -> str:
        self._issued += 1
        return f"refresh-{self._issued}"

    def hash(self, token: str) -> str:
        return f"hash:{token}"


class InMemoryIdempotencyStore(AbstractIdempotencyStore):
    """`available = False` simulates a Redis outage (fail-open)."""

    def __init__(self) -> None:
        self.records: dict[str, IdempotencyRecord] = {}
        self.available = True

    async def begin(self, key: str, fingerprint: str) -> BeginResult:
        if not self.available:
            return BeginResult(BeginOutcome.UNAVAILABLE)
        if key in self.records:
            return BeginResult(BeginOutcome.EXISTS, self.records[key])
        self.records[key] = IdempotencyRecord(
            state=IdempotencyState.PENDING, fingerprint=fingerprint
        )
        return BeginResult(BeginOutcome.STARTED)

    async def complete(self, key: str, fingerprint: str, response: JsonValue) -> None:
        if self.available:
            self.records[key] = IdempotencyRecord(
                state=IdempotencyState.COMPLETED, fingerprint=fingerprint, response=response
            )

    async def release(self, key: str) -> None:
        if self.available:
            self.records.pop(key, None)


class InMemoryRateLimiter(AbstractRateLimiter):
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        self.counts[key] = self.counts.get(key, 0) + 1
        count = self.counts[key]
        return RateLimitDecision(
            allowed=count <= limit,
            limit=limit,
            remaining=max(0, limit - count),
            retry_after=window_seconds,
        )


class FakeHealthProbe(AbstractHealthProbe):
    def __init__(self, *, healthy: bool = True, hangs: bool = False) -> None:
        self.healthy = healthy
        self.hangs = hangs

    async def check(self) -> None:
        if self.hangs:
            await asyncio.Event().wait()
        if not self.healthy:
            raise ConnectionError("dependency is down")
