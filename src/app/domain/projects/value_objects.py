"""Project value objects: immutable, self-validating, normalized on creation."""

from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from zoneinfo import ZoneInfo, available_timezones

from app.domain.common.text import bounded_text
from app.domain.projects.errors import (
    InvalidAddressError,
    InvalidProjectNameError,
    InvalidTimeZoneError,
)

PROJECT_NAME_MAX_LENGTH = 200
ADDRESS_MAX_LENGTH = 500
TIME_ZONE_MAX_LENGTH = 64


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


@cache
def _known_time_zones() -> frozenset[str]:
    # Scans the tz database once. A fixed list, not ZoneInfo(name) alone: on a
    # case-insensitive file system ZoneInfo would accept "europe/moscow" too.
    return frozenset(available_timezones())


@dataclass(frozen=True, slots=True)
class TimeZone:
    """An IANA time zone name, e.g. "Europe/Moscow": where the move takes place."""

    value: str

    def __post_init__(self) -> None:
        if self.value not in _known_time_zones():
            raise InvalidTimeZoneError(f"Unknown time zone {self.value[:TIME_ZONE_MAX_LENGTH]!r}")

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.value)

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
