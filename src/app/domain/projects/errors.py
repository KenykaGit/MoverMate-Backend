from app.domain.common.errors import DomainError


class InvalidProjectNameError(DomainError):
    """The project name does not satisfy domain rules."""


class InvalidAddressError(DomainError):
    """The address does not satisfy domain rules."""


class CannotRemoveOwnerError(DomainError):
    """The owner always stays a member of their project."""


class NotProjectOwnerError(DomainError):
    """The action is reserved to the owner of the project."""
