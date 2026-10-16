import asyncio
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.application.interfaces.password_hasher import AbstractPasswordHasher
from app.domain.users.value_objects import PasswordHash, RawPassword


class Argon2PasswordHasher(AbstractPasswordHasher):
    """Argon2id. Hashing is CPU-bound and deliberately slow, so it runs in a
    worker thread instead of blocking the event loop."""

    def __init__(self, *, time_cost: int, memory_cost_kib: int, parallelism: int) -> None:
        self._hasher = PasswordHasher(
            time_cost=time_cost, memory_cost=memory_cost_kib, parallelism=parallelism
        )
        # A real hash with the same parameters: verifying against it costs exactly
        # as much as verifying a real user's password.
        self._dummy_hash = PasswordHash(self._hasher.hash(secrets.token_urlsafe()))

    async def hash(self, password: RawPassword) -> PasswordHash:
        return await self._hash(password.value)

    async def rehash(self, password: str) -> PasswordHash:
        return await self._hash(password)

    async def _hash(self, password: str) -> PasswordHash:
        return PasswordHash(await asyncio.to_thread(self._hasher.hash, password))

    async def verify(self, password: str, password_hash: PasswordHash) -> bool:
        try:
            return await asyncio.to_thread(self._hasher.verify, password_hash.value, password)
        except VerificationError, InvalidHashError:
            return False

    async def dummy_verify(self, password: str) -> None:
        await self.verify(password, self._dummy_hash)

    def needs_rehash(self, password_hash: PasswordHash) -> bool:
        # Also true for another argon2 variant or version: anything but ours.
        try:
            return self._hasher.check_needs_rehash(password_hash.value)
        except InvalidHashError:
            return False  # cannot be verified either; nothing to upgrade
