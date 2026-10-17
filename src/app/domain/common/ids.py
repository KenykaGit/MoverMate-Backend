"""Identity generation and identifiers shared by several aggregates (shared kernel).

Domain modules must not import each other (tests/unit/test_architecture.py),
so an identifier referenced across aggregates lives here.
"""

from typing import NewType
from uuid import UUID, uuid7

UserId = NewType("UserId", UUID)
"""Identity of a User; also how refresh tokens and projects refer to people."""
ProjectId = NewType("ProjectId", UUID)
RoomId = NewType("RoomId", UUID)
BoxId = NewType("BoxId", UUID)
ItemId = NewType("ItemId", UUID)
TaskId = NewType("TaskId", UUID)


def new_id() -> UUID:
    """A new time-ordered identifier (UUIDv7, RFC 9562) for an entity.

    The standard library keeps a 42-bit counter within the millisecond, so ids
    of one process are strictly increasing: ordering by id is ordering by
    creation time.
    """
    return uuid7()
