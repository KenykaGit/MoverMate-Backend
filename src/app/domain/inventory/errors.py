from app.domain.common.errors import DomainError


class InvalidRoomNameError(DomainError):
    """The room name does not satisfy domain rules."""


class InvalidBoxNumberError(DomainError):
    """Box numbers are positive integers."""


class InvalidQrTokenError(DomainError):
    """The string cannot be a QR token of a box."""


class InvalidItemNameError(DomainError):
    """The item name does not satisfy domain rules."""


class InvalidQuantityError(DomainError):
    """The item quantity is out of range."""


class WrongRoomKindError(DomainError):
    """A box needs a destination room, an item needs a source room."""


class CrossProjectReferenceError(DomainError):
    """An object was linked to an object of another project."""


class BoxStatusTransitionError(DomainError):
    """The box cannot move to this status from its current one."""


class ItemStateTransitionError(DomainError):
    """The item cannot move to this state from its current one."""
