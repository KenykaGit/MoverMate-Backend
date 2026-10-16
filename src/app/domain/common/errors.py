class DomainError(Exception):
    """Base class for violations of business rules.

    Raised by value objects and entities. The presentation layer maps every
    subclass to a stable error code; an unmapped one is reported as
    `invalid_value`.
    """
