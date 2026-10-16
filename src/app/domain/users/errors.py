from app.domain.common.errors import DomainError


class InvalidEmailError(DomainError):
    """The email address does not satisfy domain rules."""


class InvalidUsernameError(DomainError):
    """The username does not satisfy domain rules."""


class InvalidPasswordError(DomainError):
    """The plain-text password does not satisfy the password policy."""


class InvalidPasswordHashError(DomainError):
    """The password hash is empty: a server-side problem, never the client's."""
