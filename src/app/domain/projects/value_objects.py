"""Project value objects: immutable, self-validating, normalized on creation."""

from dataclasses import dataclass
from enum import StrEnum

from app.domain.common.text import bounded_text
from app.domain.projects.errors import InvalidAddressError, InvalidProjectNameError

PROJECT_NAME_MAX_LENGTH = 200
ADDRESS_MAX_LENGTH = 500


@dataclass(frozen=True, slots=True)
class ProjectName:
    value: str

    def __post_init__(self) -> None:
        normalized = bounded_text(
            self.value,
            max_length=PROJECT_NAME_MAX_LENGTH,
            what="Project name",
            error=InvalidProjectNameError,
        )
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Address:
    """A free-form postal address, used inside the project only."""

    value: str

    def __post_init__(self) -> None:
        normalized = bounded_text(
            self.value, max_length=ADDRESS_MAX_LENGTH, what="Address", error=InvalidAddressError
        )
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


class QrCodeStrategy(StrEnum):
    """Who may open a box card by its QR code (docs/db-design.md, section 8)."""

    PROTECTED = "PROTECTED"
    """Members only; others do not learn the box exists."""
    OPEN = "OPEN"
    """Anyone may read the card; only members change it."""


class QrAccess(StrEnum):
    """What a viewer of a box card may do."""

    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    NOT_FOUND = "NOT_FOUND"
    READ = "READ"
    WRITE = "WRITE"
