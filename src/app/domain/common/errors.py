class DomainError(Exception):
    """Base class for violations of business rules.

    Raised by value objects and entities. The presentation layer maps every
    subclass to a stable error code; an unmapped one is reported as
    `invalid_value`.
    """


class InvalidReminderOffsetError(DomainError):
    """The reminder offset is out of the allowed range of hours."""


class InvalidMovingReminderOffsetError(DomainError):
    """The moving date reminder offset is out of the allowed range of days."""
