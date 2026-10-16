"""Application errors: a use case could not be carried out.

Unlike DomainError (a rule of the model is violated) these describe the outcome
of a scenario: something is missing, taken, or the caller is not allowed in.
"""

from uuid import UUID

from app.domain.users.value_objects import Username


class ApplicationError(Exception):
    """Base class for all application errors."""


class NotFoundError(ApplicationError):
    """Base class: the requested object does not exist (or is not visible)."""


class UserNotFoundError(NotFoundError):
    def __init__(self, user_id: UUID) -> None:
        super().__init__(f"User {user_id} not found")
        self.user_id = user_id


class UsernameAlreadyTakenError(ApplicationError):
    def __init__(self, username: Username) -> None:
        super().__init__(f"Username {username} is already taken")
        self.username = username


class InvalidCredentialsError(ApplicationError):
    """Wrong username or password, or a deleted account.

    Deliberately carries no details: telling these cases apart would reveal
    which usernames are registered.
    """

    def __init__(self) -> None:
        super().__init__("Invalid credentials")


class InvalidRefreshTokenError(ApplicationError):
    """Unknown, expired, revoked or reused refresh token. No details, as above."""

    def __init__(self) -> None:
        super().__init__("Invalid refresh token")


class NotAuthenticatedError(ApplicationError):
    """Missing or invalid access token, or its user no longer exists."""

    def __init__(self) -> None:
        super().__init__("Not authenticated")


class InvalidPaginationError(ApplicationError):
    """limit or offset is out of the allowed range."""


class RequestInProgressError(ApplicationError):
    """A request with the same Idempotency-Key is being processed right now."""

    def __init__(self) -> None:
        super().__init__("A request with this Idempotency-Key is in progress")


class IdempotencyKeyReusedError(ApplicationError):
    """The Idempotency-Key was already used with a different payload."""

    def __init__(self) -> None:
        super().__init__("Idempotency-Key was already used with a different payload")
