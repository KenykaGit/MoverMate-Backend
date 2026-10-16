"""User value objects: immutable, self-validating, normalized on creation."""

import re
from dataclasses import dataclass, field

from app.domain.users.errors import (
    InvalidPasswordError,
    InvalidPasswordHashError,
    InvalidUsernameError,
)

USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 32
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")

PASSWORD_MIN_LENGTH = 8
# Hashing cost grows with input size: cap it so huge passwords cannot DoS us.
PASSWORD_MAX_LENGTH = 128


@dataclass(frozen=True, slots=True)
class Username:
    """Case-insensitive username, stored trimmed and in lowercase."""

    value: str

    def __post_init__(self) -> None:
        stripped = self.value.strip()
        # Validate before lowercasing: some non-ASCII characters lowercase
        # into ASCII (e.g. KELVIN SIGN -> 'k') and would slip through.
        if not USERNAME_MIN_LENGTH <= len(stripped) <= USERNAME_MAX_LENGTH:
            raise InvalidUsernameError(
                f"Username must be between {USERNAME_MIN_LENGTH} "
                f"and {USERNAME_MAX_LENGTH} characters long"
            )
        if not _USERNAME_PATTERN.fullmatch(stripped):
            raise InvalidUsernameError(
                "Username may contain only latin letters, digits, '_', '.' and '-'"
            )
        object.__setattr__(self, "value", stripped.lower())

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class RawPassword:
    """A plain-text password that satisfies the password policy.

    Never stored and never printed: only its PasswordHash belongs to a User.
    Not stripped: whitespace is a legitimate part of a password.
    """

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if not PASSWORD_MIN_LENGTH <= len(self.value) <= PASSWORD_MAX_LENGTH:
            raise InvalidPasswordError(
                f"Password must be between {PASSWORD_MIN_LENGTH} "
                f"and {PASSWORD_MAX_LENGTH} characters long"
            )
        if self.value.isspace():
            raise InvalidPasswordError("Password must not consist of whitespace only")


@dataclass(frozen=True, slots=True)
class PasswordHash:
    """An already hashed password.

    Hashing is not a domain concern: it happens behind an application port
    and only the result is passed here.
    """

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.value or self.value.isspace():
            raise InvalidPasswordHashError("Password hash must not be empty")
