"""Inputs and outputs of the inventory use cases: rooms, boxes and items.

Every input carries `user_id`, the caller: a project and everything in it exist
only for its members. Outputs are read views; some join several aggregates
(a box with its room and creator), so they are built by the use cases from
read queries rather than from a single entity.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from app.application.common.pagination import CursorPageRequest
from app.application.common.patch import UNSET, Patch
from app.application.interfaces.item_recognizer import Photo
from app.domain.inventory.box import BoxStatus
from app.domain.inventory.item import ItemState
from app.domain.inventory.room import Room
from app.domain.inventory.value_objects import Priority, RoomKind

# ------------------------------------------------------------------ outputs


@dataclass(frozen=True, slots=True, kw_only=True)
class UserRefDTO:
    id: UUID
    username: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RoomRefDTO:
    id: UUID
    name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RoomDTO:
    id: UUID
    kind: RoomKind
    name: str

    @classmethod
    def from_entity(cls, room: Room) -> Self:
        return cls(id=room.id, kind=room.kind, name=room.name.value)


@dataclass(frozen=True, slots=True, kw_only=True)
class BoxRefDTO:
    id: UUID
    number: int
    status: BoxStatus
    destination_room: RoomRefDTO


@dataclass(frozen=True, slots=True, kw_only=True)
class BoxDTO:
    id: UUID
    number: int
    title: str | None
    is_fragile: bool
    destination_room: RoomRefDTO
    status: BoxStatus
    status_changed_at: datetime
    created_by: UserRefDTO
    qr_token: str
    item_count: int
    items_preview: tuple[str, ...]
    contents_reminder_at: datetime | None
    """Shown to the creator of the box only; None for everyone else."""
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class ItemDTO:
    id: UUID
    name: str
    quantity: int
    priority: Priority
    state: ItemState
    source_room: RoomRefDTO
    box: BoxRefDTO | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class ItemSearchDTO:
    items: tuple[ItemDTO, ...]
    has_more: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class BoxSuggestionDTO:
    box: BoxRefDTO
    matches: int
    """How many items from the lost item's source room the box holds."""


class QrAccessLevel(StrEnum):
    READ = "READ"
    WRITE = "WRITE"


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectRefDTO:
    id: UUID
    name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class QrBoxItemDTO:
    id: UUID
    name: str
    quantity: int


@dataclass(frozen=True, slots=True, kw_only=True)
class QrBoxDTO:
    id: UUID
    number: int
    title: str | None
    is_fragile: bool
    destination_room: RoomRefDTO
    status: BoxStatus
    items: tuple[QrBoxItemDTO, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class BoxByQrDTO:
    access: QrAccessLevel
    project: ProjectRefDTO
    box: QrBoxDTO


# ------------------------------------------------------------------- inputs


@dataclass(frozen=True, slots=True, kw_only=True)
class ListRoomsDTO:
    user_id: UUID
    project_id: UUID
    kind: RoomKind | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateRoomDTO:
    user_id: UUID
    project_id: UUID
    kind: RoomKind
    name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RenameRoomDTO:
    user_id: UUID
    room_id: UUID
    name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class DeleteRoomDTO:
    user_id: UUID
    room_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class NewItemEntryDTO:
    name: str
    quantity: int = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class NewItemsDTO:
    """Items from one room created together; into `box_id` when given."""

    source_room_id: UUID
    priority: Priority = Priority.MEDIUM
    entries: tuple[NewItemEntryDTO, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ListBoxesDTO:
    user_id: UUID
    project_id: UUID
    statuses: tuple[BoxStatus, ...] = ()
    """Any of these; all statuses when empty."""
    destination_room_id: UUID | None = None
    number: int | None = None
    page: CursorPageRequest = field(default_factory=CursorPageRequest)


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateBoxDTO:
    user_id: UUID
    project_id: UUID
    destination_room_id: UUID
    title: str | None = None
    is_fragile: bool = False
    items: NewItemsDTO | None = None
    """Packed into the new box in the same transaction."""


@dataclass(frozen=True, slots=True, kw_only=True)
class GetBoxDTO:
    user_id: UUID
    box_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class GetBoxByQrDTO:
    user_id: UUID | None
    """None: an anonymous caller."""
    qr_token: str


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdateBoxDTO:
    """A partial update: UNSET fields stay as they are."""

    user_id: UUID
    box_id: UUID
    title: Patch[str | None] = UNSET
    is_fragile: Patch[bool] = UNSET
    destination_room_id: Patch[UUID] = UNSET


@dataclass(frozen=True, slots=True, kw_only=True)
class ChangeBoxStatusDTO:
    user_id: UUID
    box_id: UUID
    status: BoxStatus


@dataclass(frozen=True, slots=True, kw_only=True)
class DeleteBoxDTO:
    user_id: UUID
    box_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class ScheduleContentsReminderDTO:
    user_id: UUID
    box_id: UUID
    remind_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class CancelContentsReminderDTO:
    user_id: UUID
    box_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class ListItemsDTO:
    user_id: UUID
    project_id: UUID
    states: tuple[ItemState, ...] = ()
    """Any of these; all states when empty."""
    priority: Priority | None = None
    box_id: UUID | None = None
    source_room_id: UUID | None = None
    page: CursorPageRequest = field(default_factory=CursorPageRequest)


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateItemDTO:
    user_id: UUID
    project_id: UUID
    name: str
    source_room_id: UUID
    quantity: int = 1
    priority: Priority = Priority.MEDIUM
    box_id: UUID | None = None
    """Created IN_BOX when given, TO_PACK otherwise."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateItemsDTO:
    user_id: UUID
    project_id: UUID
    items: NewItemsDTO
    box_id: UUID | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class GetItemDTO:
    user_id: UUID
    item_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdateItemDTO:
    """A partial update: UNSET fields stay as they are."""

    user_id: UUID
    item_id: UUID
    name: Patch[str] = UNSET
    quantity: Patch[int] = UNSET
    priority: Patch[Priority] = UNSET
    source_room_id: Patch[UUID] = UNSET


@dataclass(frozen=True, slots=True, kw_only=True)
class ChangeItemStateDTO:
    user_id: UUID
    item_id: UUID
    state: ItemState
    box_id: UUID | None = None
    """Required for IN_BOX, absent otherwise."""


@dataclass(frozen=True, slots=True, kw_only=True)
class DeleteItemDTO:
    user_id: UUID
    item_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchItemsDTO:
    user_id: UUID
    project_id: UUID
    query: str
    limit: int
    offset: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class SuggestBoxesDTO:
    user_id: UUID
    item_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class ScanItemsDTO:
    user_id: UUID
    project_id: UUID
    photo: Photo


@dataclass(frozen=True, slots=True, kw_only=True)
class ScannedItemDTO:
    """A suggestion, not an item: the client shows the list, the user corrects it
    and saves it with createItems."""

    name: str
    quantity: int
