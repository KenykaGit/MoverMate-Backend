"""Request and response shapes of rooms, boxes and items (api-endpoints.md, sections 2, 5-7, 9).

Component names follow the frontend contract (Room, Box, Item...): the generated
SDK takes its type names from them. Only the shape of JSON is checked here; the
rules (lengths, ranges, kinds of rooms, transitions) are the domain's.
"""

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field

from app.application.common.pagination import CursorPage as CursorPageDTO
from app.application.common.patch import Unset
from app.application.inventory.dto import (
    BoxByQrDTO,
    BoxDTO,
    BoxRefDTO,
    BoxSuggestionDTO,
    ChangeBoxStatusDTO,
    ChangeItemStateDTO,
    CreateBoxDTO,
    CreateItemDTO,
    CreateItemsDTO,
    CreateRoomDTO,
    ItemDTO,
    ItemSearchDTO,
    NewItemEntryDTO,
    NewItemsDTO,
    QrAccessLevel,
    QrBoxDTO,
    RenameRoomDTO,
    RoomDTO,
    RoomRefDTO,
    ScannedItemDTO,
    ScheduleContentsReminderDTO,
    UpdateBoxDTO,
    UpdateItemDTO,
    UserRefDTO,
)
from app.domain.inventory.box import BoxStatus
from app.domain.inventory.item import ItemState
from app.domain.inventory.value_objects import Priority, RoomKind
from app.presentation.http.schemas.common import PatchRequest, RequestSchema

BATCH_MAX_ENTRIES = 100


class ItemPriority(StrEnum):
    """Item priority by name: stored as a number, published as a string."""

    FIRST_NECESSITY = "FIRST_NECESSITY"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

    @classmethod
    def of(cls, priority: Priority) -> ItemPriority:
        return cls(priority.name)

    def to_domain(self) -> Priority:
        return Priority[self.value]


# ---------------------------------------------------------------- responses


class UserRef(BaseModel):
    id: UUID
    username: str

    @classmethod
    def from_dto(cls, user: UserRefDTO) -> Self:
        return cls(id=user.id, username=user.username)


class RoomRef(BaseModel):
    id: UUID
    name: str

    @classmethod
    def from_dto(cls, room: RoomRefDTO) -> Self:
        return cls(id=room.id, name=room.name)


class Room(BaseModel):
    id: UUID
    kind: RoomKind
    name: str

    @classmethod
    def from_dto(cls, room: RoomDTO) -> Self:
        return cls(id=room.id, kind=room.kind, name=room.name)


class BoxRef(BaseModel):
    id: UUID
    number: int
    status: BoxStatus
    destination_room: RoomRef

    @classmethod
    def from_dto(cls, box: BoxRefDTO) -> Self:
        return cls(
            id=box.id,
            number=box.number,
            status=box.status,
            destination_room=RoomRef.from_dto(box.destination_room),
        )


class Box(BaseModel):
    id: UUID
    number: int
    title: str | None
    is_fragile: bool
    destination_room: RoomRef
    status: BoxStatus
    status_changed_at: datetime
    created_by: UserRef
    qr_token: str
    item_count: int = Field(description="Active items IN_BOX")
    items_preview: list[str] = Field(description="Up to 3 item names for the list card")
    contents_reminder_at: datetime | None = Field(
        description="Only for the creator of the box; null for everyone else"
    )
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_dto(cls, box: BoxDTO) -> Self:
        return cls(
            id=box.id,
            number=box.number,
            title=box.title,
            is_fragile=box.is_fragile,
            destination_room=RoomRef.from_dto(box.destination_room),
            status=box.status,
            status_changed_at=box.status_changed_at,
            created_by=UserRef.from_dto(box.created_by),
            qr_token=box.qr_token,
            item_count=box.item_count,
            items_preview=list(box.items_preview),
            contents_reminder_at=box.contents_reminder_at,
            created_at=box.created_at,
            updated_at=box.updated_at,
        )


class Item(BaseModel):
    id: UUID
    name: str
    quantity: int
    priority: ItemPriority
    state: ItemState
    source_room: RoomRef
    box: BoxRef | None = Field(description="Set only when the item is IN_BOX")
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_dto(cls, item: ItemDTO) -> Self:
        return cls(
            id=item.id,
            name=item.name,
            quantity=item.quantity,
            priority=ItemPriority.of(item.priority),
            state=item.state,
            source_room=RoomRef.from_dto(item.source_room),
            box=None if item.box is None else BoxRef.from_dto(item.box),
            created_at=item.created_at,
            updated_at=item.updated_at,
        )


class CursorPage[T](BaseModel):
    results: list[T]
    next_cursor: str | None = Field(description="Opaque; null on the last page")


class BoxPage(CursorPage[Box]):
    @classmethod
    def from_dto(cls, page: CursorPageDTO[BoxDTO]) -> Self:
        return cls(results=[Box.from_dto(box) for box in page.items], next_cursor=page.next_cursor)


class ItemPage(CursorPage[Item]):
    @classmethod
    def from_dto(cls, page: CursorPageDTO[ItemDTO]) -> Self:
        return cls(
            results=[Item.from_dto(item) for item in page.items], next_cursor=page.next_cursor
        )


class ItemSearchResult(BaseModel):
    """Ranked by relevance, so paged by limit/offset rather than by cursor."""

    results: list[Item]
    has_more: bool

    @classmethod
    def from_dto(cls, found: ItemSearchDTO) -> Self:
        return cls(results=[Item.from_dto(item) for item in found.items], has_more=found.has_more)


class ItemSuggestion(BaseModel):
    """A box that holds other items from the lost item's source room."""

    box: BoxRef
    matches: int

    @classmethod
    def from_dto(cls, suggestion: BoxSuggestionDTO) -> Self:
        return cls(box=BoxRef.from_dto(suggestion.box), matches=suggestion.matches)


class ScannedItem(BaseModel):
    """The same shape as an ItemEntry of createItems."""

    name: str
    quantity: int

    @classmethod
    def from_dto(cls, item: ScannedItemDTO) -> Self:
        return cls(name=item.name, quantity=item.quantity)


class ItemScan(BaseModel):
    """Suggestions from a photo, in the order the model listed them; may be empty."""

    items: list[ScannedItem]

    @classmethod
    def from_dtos(cls, items: list[ScannedItemDTO]) -> Self:
        return cls(items=[ScannedItem.from_dto(item) for item in items])


class ProjectRef(BaseModel):
    id: UUID
    name: str


class QrBoxItem(BaseModel):
    id: UUID
    name: str
    quantity: int


class QrBox(BaseModel):
    id: UUID
    number: int
    title: str | None
    is_fragile: bool
    destination_room: RoomRef
    status: BoxStatus
    items: list[QrBoxItem]

    @classmethod
    def from_dto(cls, box: QrBoxDTO) -> Self:
        return cls(
            id=box.id,
            number=box.number,
            title=box.title,
            is_fragile=box.is_fragile,
            destination_room=RoomRef.from_dto(box.destination_room),
            status=box.status,
            items=[QrBoxItem(id=i.id, name=i.name, quantity=i.quantity) for i in box.items],
        )


class BoxByQr(BaseModel):
    access: QrAccessLevel
    project: ProjectRef
    box: QrBox

    @classmethod
    def from_dto(cls, found: BoxByQrDTO) -> Self:
        return cls(
            access=found.access,
            project=ProjectRef(id=found.project.id, name=found.project.name),
            box=QrBox.from_dto(found.box),
        )


# ----------------------------------------------------------------- requests


class CreateRoomRequest(RequestSchema):
    kind: RoomKind
    name: str

    def to_dto(self, *, user_id: UUID, project_id: UUID) -> CreateRoomDTO:
        return CreateRoomDTO(user_id=user_id, project_id=project_id, kind=self.kind, name=self.name)


class UpdateRoomRequest(RequestSchema):
    """The kind of a room never changes: boxes and items rely on it."""

    name: str

    def to_dto(self, *, user_id: UUID, room_id: UUID) -> RenameRoomDTO:
        return RenameRoomDTO(user_id=user_id, room_id=room_id, name=self.name)


class ItemEntry(RequestSchema):
    name: str
    quantity: int = 1

    def to_dto(self) -> NewItemEntryDTO:
        return NewItemEntryDTO(name=self.name, quantity=self.quantity)


class BoxItemsRequest(RequestSchema):
    """Items packed into a new box in the same transaction: created IN_BOX."""

    source_room_id: UUID
    priority: ItemPriority = ItemPriority.MEDIUM
    entries: list[ItemEntry] = Field(min_length=1, max_length=BATCH_MAX_ENTRIES)

    def to_dto(self) -> NewItemsDTO:
        return NewItemsDTO(
            source_room_id=self.source_room_id,
            priority=self.priority.to_domain(),
            entries=tuple(entry.to_dto() for entry in self.entries),
        )


class CreateBoxRequest(RequestSchema):
    destination_room_id: UUID
    title: str | None = None
    is_fragile: bool = False
    items: BoxItemsRequest | None = None

    def to_dto(self, *, user_id: UUID, project_id: UUID) -> CreateBoxDTO:
        return CreateBoxDTO(
            user_id=user_id,
            project_id=project_id,
            destination_room_id=self.destination_room_id,
            title=self.title,
            is_fragile=self.is_fragile,
            items=None if self.items is None else self.items.to_dto(),
        )


class UpdateBoxRequest(PatchRequest):
    """Partial update: absent fields stay; null clears `title` and is rejected elsewhere."""

    title: str | None = None
    is_fragile: bool | None = None
    destination_room_id: UUID | None = None

    def to_dto(self, *, user_id: UUID, box_id: UUID) -> UpdateBoxDTO:
        return UpdateBoxDTO(
            user_id=user_id,
            box_id=box_id,
            title=self.sent("title", self.title),
            is_fragile=self.sent_not_null("is_fragile", self.is_fragile),
            destination_room_id=self.sent_not_null("destination_room_id", self.destination_room_id),
        )


class SetBoxStatusRequest(RequestSchema):
    status: BoxStatus

    def to_dto(self, *, user_id: UUID, box_id: UUID) -> ChangeBoxStatusDTO:
        return ChangeBoxStatusDTO(user_id=user_id, box_id=box_id, status=self.status)


class SetContentsReminderRequest(RequestSchema):
    remind_at: AwareDatetime

    def to_dto(self, *, user_id: UUID, box_id: UUID) -> ScheduleContentsReminderDTO:
        return ScheduleContentsReminderDTO(user_id=user_id, box_id=box_id, remind_at=self.remind_at)


class CreateItemRequest(RequestSchema):
    """Created IN_BOX when box_id is given, TO_PACK otherwise."""

    name: str
    source_room_id: UUID
    quantity: int = 1
    priority: ItemPriority = ItemPriority.MEDIUM
    box_id: UUID | None = None

    def to_dto(self, *, user_id: UUID, project_id: UUID) -> CreateItemDTO:
        return CreateItemDTO(
            user_id=user_id,
            project_id=project_id,
            name=self.name,
            source_room_id=self.source_room_id,
            quantity=self.quantity,
            priority=self.priority.to_domain(),
            box_id=self.box_id,
        )


class CreateItemsRequest(RequestSchema):
    """All items or none."""

    source_room_id: UUID
    priority: ItemPriority = ItemPriority.MEDIUM
    box_id: UUID | None = None
    entries: list[ItemEntry] = Field(min_length=1, max_length=BATCH_MAX_ENTRIES)

    def to_dto(self, *, user_id: UUID, project_id: UUID) -> CreateItemsDTO:
        return CreateItemsDTO(
            user_id=user_id,
            project_id=project_id,
            box_id=self.box_id,
            items=NewItemsDTO(
                source_room_id=self.source_room_id,
                priority=self.priority.to_domain(),
                entries=tuple(entry.to_dto() for entry in self.entries),
            ),
        )


class UpdateItemRequest(PatchRequest):
    """Partial update: absent fields stay as they are; none of them may be null."""

    name: str | None = None
    quantity: int | None = None
    priority: ItemPriority | None = None
    source_room_id: UUID | None = None

    def to_dto(self, *, user_id: UUID, item_id: UUID) -> UpdateItemDTO:
        priority = self.sent_not_null("priority", self.priority)
        return UpdateItemDTO(
            user_id=user_id,
            item_id=item_id,
            name=self.sent_not_null("name", self.name),
            quantity=self.sent_not_null("quantity", self.quantity),
            priority=priority if isinstance(priority, Unset) else priority.to_domain(),
            source_room_id=self.sent_not_null("source_room_id", self.source_room_id),
        )


class SetItemStateRequest(RequestSchema):
    """`box_id` is required for IN_BOX and must be absent otherwise."""

    state: ItemState
    box_id: UUID | None = None

    def to_dto(self, *, user_id: UUID, item_id: UUID) -> ChangeItemStateDTO:
        return ChangeItemStateDTO(
            user_id=user_id, item_id=item_id, state=self.state, box_id=self.box_id
        )
