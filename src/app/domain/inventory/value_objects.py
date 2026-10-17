"""Inventory value objects: immutable, self-validating, normalized on creation."""

import re
import secrets
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Self

from app.domain.common.text import bounded_text
from app.domain.inventory.errors import (
    InvalidBoxNumberError,
    InvalidItemNameError,
    InvalidQrTokenError,
    InvalidQuantityError,
    InvalidRoomNameError,
)

ROOM_NAME_MAX_LENGTH = 100
ITEM_NAME_MAX_LENGTH = 200
QUANTITY_MAX = 10_000

QR_TOKEN_BYTES = 16  # 128 bits: cannot be guessed or enumerated
QR_TOKEN_LENGTH = 22  # base64url of 16 bytes, without padding
_QR_TOKEN_PATTERN = re.compile(rf"[A-Za-z0-9_-]{{{QR_TOKEN_LENGTH}}}")


class RoomKind(StrEnum):
    SOURCE = "SOURCE"
    """A room of the old home: items come from it."""
    DESTINATION = "DESTINATION"
    """A room of the new home: boxes go to it."""


@dataclass(frozen=True, slots=True)
class RoomName:
    value: str

    def __post_init__(self) -> None:
        normalized = bounded_text(
            self.value,
            max_length=ROOM_NAME_MAX_LENGTH,
            what="Room name",
            error=InvalidRoomNameError,
        )
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, order=True)
class BoxNumber:
    """The number written on a box: unique among the active boxes of a project."""

    value: int

    def __post_init__(self) -> None:
        if self.value < 1:
            raise InvalidBoxNumberError("Box number must be a positive integer")

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class QrToken:
    """The public, unguessable id of a box encoded in its QR code.

    Tied to the box, not to its number: a label of a deleted box never opens
    the box that later gets the same number.
    """

    value: str

    def __post_init__(self) -> None:
        if not _QR_TOKEN_PATTERN.fullmatch(self.value):
            raise InvalidQrTokenError("Not a box QR token")

    @classmethod
    def generate(cls) -> Self:
        return cls(secrets.token_urlsafe(QR_TOKEN_BYTES))

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ItemName:
    value: str

    def __post_init__(self) -> None:
        normalized = bounded_text(
            self.value,
            max_length=ITEM_NAME_MAX_LENGTH,
            what="Item name",
            error=InvalidItemNameError,
        )
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Quantity:
    value: int

    def __post_init__(self) -> None:
        if not 1 <= self.value <= QUANTITY_MAX:
            raise InvalidQuantityError(f"Quantity must be between 1 and {QUANTITY_MAX}")


class Priority(IntEnum):
    """Packing order: lower values first. Stored as the number, so sorting needs no CASE."""

    FIRST_NECESSITY = 0
    """Also the first-day checklist."""
    HIGH = 1
    MEDIUM = 2
    LOW = 3
