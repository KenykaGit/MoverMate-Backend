from abc import ABC, abstractmethod

from app.domain.users.value_objects import PasswordHash, RawPassword


class AbstractPasswordHasher(ABC):
    @abstractmethod
    async def hash(self, password: RawPassword) -> PasswordHash:
        """Hash a password that satisfies the current password policy."""

    @abstractmethod
    async def verify(self, password: str, password_hash: PasswordHash) -> bool:
        """Check a password against a stored hash.

        Takes a plain `str`, not RawPassword: the policy applies when a password
        is set, so users with passwords from an older, weaker policy can still
        log in. False for a wrong password and for a malformed hash alike.
        """

    @abstractmethod
    async def dummy_verify(self, password: str) -> None:
        """Take as long as verify() when there is no hash to check against.

        Call it for an unknown login: otherwise the response time reveals
        which logins are registered.
        """

    @abstractmethod
    def needs_rehash(self, password_hash: PasswordHash) -> bool:
        """True if the hash was made with other (e.g. weaker) parameters than now."""

    @abstractmethod
    async def rehash(self, password: str) -> PasswordHash:
        """Hash an already verified password again with the current parameters.

        Takes a plain `str` for the same reason as verify(): the password was
        accepted when it was set, a newer policy must not lock its owner out.
        """
