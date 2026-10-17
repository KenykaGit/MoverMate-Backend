from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from app.domain.common.entity import Entity
from app.domain.common.ids import BoxId, ItemId, ProjectId, RoomId, new_id
from app.domain.inventory.box import Box
from app.domain.inventory.errors import CrossProjectReferenceError, ItemStateTransitionError
from app.domain.inventory.room import Room
from app.domain.inventory.value_objects import ItemName, Priority, Quantity, RoomKind


class ItemState(StrEnum):
    TO_PACK = "TO_PACK"
    """Recorded, still in its source room."""
    IN_BOX = "IN_BOX"
    """In a box: the only state with a box_id."""
    TAKEN_OUT = "TAKEN_OUT"
    """Taken out of its box at the new home."""
    LOST = "LOST"


_S = ItemState
ITEM_TRANSITIONS: frozenset[tuple[ItemState, ItemState]] = frozenset(
    {
        (_S.TO_PACK, _S.IN_BOX),  # packed
        (_S.IN_BOX, _S.IN_BOX),  # moved to another box
        (_S.IN_BOX, _S.TO_PACK),  # taken back out before the move
        (_S.IN_BOX, _S.TAKEN_OUT),  # unpacked
        (_S.TAKEN_OUT, _S.IN_BOX),  # packed again
        (_S.TO_PACK, _S.LOST),
        (_S.IN_BOX, _S.LOST),
        (_S.TAKEN_OUT, _S.LOST),
        (_S.LOST, _S.TO_PACK),  # found in its source room
        (_S.LOST, _S.IN_BOX),  # found in a box, any box
        (_S.LOST, _S.TAKEN_OUT),  # found at the new home
    }
)
"""Allowed (from, to) state pairs (docs/db-design.md, section 5.2).

The database keeps a copy of this matrix; a test checks that both agree.
"""


@dataclass(frozen=True, slots=True)
class ItemStateChange:
    """What a state change did: the audit log records losses and finds."""

    from_state: ItemState
    to_state: ItemState

    @property
    def is_loss(self) -> bool:
        return self.to_state is ItemState.LOST

    @property
    def is_find(self) -> bool:
        return self.from_state is ItemState.LOST


@dataclass(eq=False, kw_only=True, slots=True)
class Item(Entity[ItemId]):
    """A thing being moved.

    Invariant: `box_id` is set exactly when `state` is IN_BOX (a CHECK in the
    database too). Only the methods below change the state, and they keep it.
    """

    project_id: ProjectId
    source_room_id: RoomId
    """Where the item comes from; what suggestions for a lost item are based on."""
    box_id: BoxId | None
    name: ItemName
    quantity: Quantity
    priority: Priority
    state: ItemState
    is_active: bool

    @classmethod
    def create(
        cls,
        *,
        project_id: ProjectId,
        source: Room,
        name: ItemName,
        quantity: Quantity | None = None,
        priority: Priority = Priority.MEDIUM,
    ) -> Self:
        """A new item waiting to be packed in its `source` room."""
        source.ensure_usable_as(RoomKind.SOURCE, project_id=project_id)
        return cls(
            id=ItemId(new_id()),
            project_id=project_id,
            source_room_id=source.id,
            box_id=None,
            name=name,
            quantity=quantity or Quantity(1),
            priority=priority,
            state=ItemState.TO_PACK,
            is_active=True,
        )

    def put_into(self, box: Box) -> ItemStateChange | None:
        """Pack the item into `box`, whatever the box status. The same box again is a no-op."""
        if box.project_id != self.project_id:
            raise CrossProjectReferenceError("The box belongs to another project")
        if self.box_id == box.id:
            return None
        return self._move_to(ItemState.IN_BOX, box_id=box.id)

    def take_out(self) -> ItemStateChange | None:
        """Unpacked at the new home."""
        return self._move_to(ItemState.TAKEN_OUT)

    def return_to_room(self) -> ItemStateChange | None:
        """Back to its source room, waiting to be packed."""
        return self._move_to(ItemState.TO_PACK)

    def mark_lost(self) -> ItemStateChange | None:
        """Lost from any state; the box it was in is forgotten."""
        return self._move_to(ItemState.LOST)

    def change_source_room(self, room: Room) -> None:
        room.ensure_usable_as(RoomKind.SOURCE, project_id=self.project_id)
        self.source_room_id = room.id

    def rename(self, name: ItemName) -> None:
        self.name = name

    def change_quantity(self, quantity: Quantity) -> None:
        self.quantity = quantity

    def set_priority(self, priority: Priority) -> None:
        self.priority = priority

    def soft_delete(self) -> None:
        """Idempotent."""
        self.is_active = False

    def _move_to(self, state: ItemState, *, box_id: BoxId | None = None) -> ItemStateChange | None:
        """Change the state, keeping the box_id invariant. The current state again is a no-op.

        Raises:
            ItemStateTransitionError: the matrix does not allow the move.
        """
        if state is self.state and state is not ItemState.IN_BOX:
            return None
        if (self.state, state) not in ITEM_TRANSITIONS:
            raise ItemStateTransitionError(f"An item cannot go from {self.state} to {state}")
        change = ItemStateChange(from_state=self.state, to_state=state)
        self.state = state
        self.box_id = box_id
        return change
