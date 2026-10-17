from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from itertools import pairwise
from typing import Self

from app.domain.common.entity import Entity
from app.domain.common.ids import BoxId, ProjectId, RoomId, UserId, new_id
from app.domain.inventory.errors import BoxStatusTransitionError
from app.domain.inventory.room import Room
from app.domain.inventory.value_objects import BoxNumber, BoxTitle, QrToken, RoomKind


class BoxStatus(StrEnum):
    """Lifecycle of a box, in order: PACKING -> PACKED -> LOADED -> DELIVERED -> UNPACKED."""

    PACKING = "PACKING"
    PACKED = "PACKED"
    LOADED = "LOADED"
    DELIVERED = "DELIVERED"
    UNPACKED = "UNPACKED"

    @property
    def position(self) -> int:
        return list(BoxStatus).index(self)


_STEPS_FORWARD = frozenset(pairwise(BoxStatus))
BOX_TRANSITIONS: frozenset[tuple[BoxStatus, BoxStatus]] = _STEPS_FORWARD | {
    (later, earlier) for earlier, later in _STEPS_FORWARD
}
"""Allowed (from, to) pairs: one step forward, or one step back (a roll-back).

The database keeps a copy of this matrix; a test checks that both agree.
"""


@dataclass(frozen=True, slots=True)
class BoxStatusChange:
    """What a status change did: the audit log records it."""

    from_status: BoxStatus
    to_status: BoxStatus

    @property
    def is_rollback(self) -> bool:
        return self.to_status.position < self.from_status.position


@dataclass(eq=False, kw_only=True, slots=True)
class Box(Entity[BoxId]):
    project_id: ProjectId
    number: BoxNumber
    """Assigned by the application: the lowest number free among active boxes."""
    qr_token: QrToken
    destination_room_id: RoomId
    created_by: UserId
    """Receives the reminder to fill in the contents."""
    title: BoxTitle | None
    """A short description that tells boxes apart in a long list ("Dishes for day one")."""
    is_fragile: bool
    """Printed on the label."""
    status: BoxStatus
    status_changed_at: datetime
    is_active: bool
    """Soft-delete flag; deleting a box frees its number."""

    @classmethod
    def create(
        cls,
        *,
        project_id: ProjectId,
        number: BoxNumber,
        destination: Room,
        created_by: UserId,
        now: datetime,
        title: BoxTitle | None = None,
        is_fragile: bool = False,
    ) -> Self:
        """A new box being packed, headed for `destination`."""
        destination.ensure_usable_as(RoomKind.DESTINATION, project_id=project_id)
        return cls(
            id=BoxId(new_id()),
            project_id=project_id,
            number=number,
            qr_token=QrToken.generate(),
            destination_room_id=destination.id,
            created_by=created_by,
            title=title,
            is_fragile=is_fragile,
            status=BoxStatus.PACKING,
            status_changed_at=now,
            is_active=True,
        )

    def change_destination(self, room: Room) -> None:
        room.ensure_usable_as(RoomKind.DESTINATION, project_id=self.project_id)
        self.destination_room_id = room.id

    def change_title(self, title: BoxTitle | None) -> None:
        self.title = title

    def set_fragile(self, is_fragile: bool) -> None:
        self.is_fragile = is_fragile

    def change_status(self, status: BoxStatus, *, now: datetime) -> BoxStatusChange | None:
        """Move one step forward or back. The current status again is a no-op (None).

        Raises:
            BoxStatusTransitionError: a step is skipped.
        """
        if status is self.status:
            return None
        if (self.status, status) not in BOX_TRANSITIONS:
            raise BoxStatusTransitionError(
                f"A box cannot go from {self.status} to {status}: one step at a time"
            )
        change = BoxStatusChange(from_status=self.status, to_status=status)
        self.status = status
        self.status_changed_at = now
        return change

    def soft_delete(self) -> None:
        """Idempotent. The items inside are deleted with it by the use case."""
        self.is_active = False
