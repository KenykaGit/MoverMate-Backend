"""Mappers of the inventory aggregates: rooms, boxes and items."""

from app.domain.common.ids import BoxId, ItemId, ProjectId, RoomId, UserId
from app.domain.inventory.box import Box, BoxStatus
from app.domain.inventory.item import Item, ItemState
from app.domain.inventory.room import Room
from app.domain.inventory.value_objects import (
    BoxNumber,
    ItemName,
    Priority,
    QrToken,
    Quantity,
    RoomKind,
    RoomName,
)
from app.infrastructure.db.models.box import BoxModel
from app.infrastructure.db.models.item import ItemModel
from app.infrastructure.db.models.room import RoomModel


class RoomMapper:
    @staticmethod
    def to_domain(model: RoomModel) -> Room:
        return Room(
            id=RoomId(model.id),
            project_id=ProjectId(model.project_id),
            kind=RoomKind(model.kind),
            name=RoomName(model.name),
            is_active=model.is_active,
        )

    @staticmethod
    def to_model(room: Room) -> RoomModel:
        return RoomModel(
            id=room.id,
            project_id=room.project_id,
            kind=room.kind.value,
            name=room.name.value,
            is_active=room.is_active,
        )


class BoxMapper:
    @staticmethod
    def to_domain(model: BoxModel) -> Box:
        return Box(
            id=BoxId(model.id),
            project_id=ProjectId(model.project_id),
            number=BoxNumber(model.number),
            qr_token=QrToken(model.qr_token),
            destination_room_id=RoomId(model.destination_room_id),
            created_by=UserId(model.created_by_id),
            status=BoxStatus(model.status),
            status_changed_at=model.status_changed_at,
            is_active=model.is_active,
        )

    @staticmethod
    def to_model(box: Box) -> BoxModel:
        return BoxModel(
            id=box.id,
            project_id=box.project_id,
            number=box.number.value,
            qr_token=box.qr_token.value,
            destination_room_id=box.destination_room_id,
            created_by_id=box.created_by,
            status=box.status.value,
            status_changed_at=box.status_changed_at,
            is_active=box.is_active,
        )


class ItemMapper:
    @staticmethod
    def to_domain(model: ItemModel) -> Item:
        return Item(
            id=ItemId(model.id),
            project_id=ProjectId(model.project_id),
            source_room_id=RoomId(model.source_room_id),
            box_id=None if model.box_id is None else BoxId(model.box_id),
            name=ItemName(model.name),
            quantity=Quantity(model.quantity),
            priority=Priority(model.priority),
            state=ItemState(model.state),
            is_active=model.is_active,
        )

    @staticmethod
    def to_model(item: Item) -> ItemModel:
        return ItemModel(
            id=item.id,
            project_id=item.project_id,
            source_room_id=item.source_room_id,
            box_id=item.box_id,
            name=item.name.value,
            quantity=item.quantity.value,
            priority=item.priority.value,
            state=item.state.value,
            is_active=item.is_active,
        )
