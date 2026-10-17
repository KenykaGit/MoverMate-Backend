"""Views of the inventory screens: a few queries per page, never one per object."""

from collections import defaultdict
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Row, Select, and_, func, literal_column, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.application.common.pagination import CursorPage, CursorPageRequest
from app.application.exceptions import InvalidPaginationError
from app.application.inventory.dto import (
    BoxDTO,
    BoxRefDTO,
    BoxSuggestionDTO,
    ItemDTO,
    ItemSearchDTO,
    QrBoxDTO,
    QrBoxItemDTO,
    RoomRefDTO,
    UserRefDTO,
)
from app.application.inventory.read_model import AbstractInventoryReadModel
from app.domain.common.ids import BoxId, ItemId, ProjectId, RoomId, UserId
from app.domain.inventory.box import BoxStatus
from app.domain.inventory.item import ItemState
from app.domain.inventory.value_objects import Priority
from app.domain.notifications.notification import NotificationStatus, NotificationType
from app.infrastructure.db.cursor import decode_cursor, encode_cursor
from app.infrastructure.db.models.box import BoxModel
from app.infrastructure.db.models.item import ItemModel
from app.infrastructure.db.models.notification import NotificationModel
from app.infrastructure.db.models.room import RoomModel
from app.infrastructure.db.models.user import UserModel

PREVIEW_SIZE = 3

# The same expression as the GIN index ix_items_name_fts, with the configuration as
# a literal: a bound parameter would keep the planner from using the index.
_RUSSIAN: ColumnElement[Any] = literal_column("'russian'::regconfig")
_ITEM_VECTOR = func.to_tsvector(_RUSSIAN, ItemModel.name)

_DESTINATION = aliased(RoomModel, name="destination_room")
_SOURCE = aliased(RoomModel, name="source_room")
_ITEM_ORDER = (ItemModel.priority, func.lower(ItemModel.name), ItemModel.id)


class SqlAlchemyInventoryReadModel(AbstractInventoryReadModel):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ boxes

    async def box(self, box_id: BoxId, *, viewer_id: UserId) -> BoxDTO | None:
        rows = (await self._session.execute(_boxes(viewer_id).where(BoxModel.id == box_id))).all()
        views = await self._box_views(rows)
        return views[0] if views else None

    async def boxes(
        self,
        project_id: ProjectId,
        *,
        viewer_id: UserId,
        statuses: Sequence[BoxStatus],
        destination_room_id: RoomId | None,
        number: int | None,
        page: CursorPageRequest,
    ) -> CursorPage[BoxDTO]:
        query = _boxes(viewer_id).where(BoxModel.project_id == project_id)
        if statuses:
            query = query.where(BoxModel.status.in_([status.value for status in statuses]))
        if destination_room_id is not None:
            query = query.where(BoxModel.destination_room_id == destination_room_id)
        if number is not None:
            query = query.where(BoxModel.number == number)
        if page.after is not None:
            (last_number,) = decode_cursor(page.after, length=1)
            query = query.where(BoxModel.number > _int(last_number))

        rows = (
            await self._session.execute(query.order_by(BoxModel.number).limit(page.limit + 1))
        ).all()
        views = await self._box_views(rows[: page.limit])
        has_more = len(rows) > page.limit
        return CursorPage(
            items=tuple(views),
            next_cursor=encode_cursor([views[-1].number]) if has_more else None,
        )

    async def qr_box(self, box_id: BoxId) -> QrBoxDTO | None:
        row = (
            await self._session.execute(
                select(
                    BoxModel.id,
                    BoxModel.number,
                    BoxModel.title,
                    BoxModel.is_fragile,
                    BoxModel.status,
                    _DESTINATION.id.label("room_id"),
                    _DESTINATION.name.label("room_name"),
                )
                .join(_DESTINATION, _DESTINATION.id == BoxModel.destination_room_id)
                .where(BoxModel.id == box_id, BoxModel.is_active)
            )
        ).one_or_none()
        if row is None:
            return None
        items = await self._session.execute(
            select(ItemModel.id, ItemModel.name, ItemModel.quantity)
            .where(ItemModel.box_id == box_id, ItemModel.is_active)
            .order_by(*_ITEM_ORDER)
        )
        return QrBoxDTO(
            id=row.id,
            number=row.number,
            title=row.title,
            is_fragile=row.is_fragile,
            destination_room=RoomRefDTO(id=row.room_id, name=row.room_name),
            status=BoxStatus(row.status),
            items=tuple(QrBoxItemDTO(id=i.id, name=i.name, quantity=i.quantity) for i in items),
        )

    async def _box_views(self, rows: Sequence[Row[Any]]) -> list[BoxDTO]:
        """Box rows with the count and a preview of their contents: one more query."""
        contents = await self._contents([row.id for row in rows])
        views = []
        for row in rows:
            count, preview = contents.get(row.id, (0, ()))
            views.append(
                BoxDTO(
                    id=row.id,
                    number=row.number,
                    title=row.title,
                    is_fragile=row.is_fragile,
                    destination_room=RoomRefDTO(id=row.room_id, name=row.room_name),
                    status=BoxStatus(row.status),
                    status_changed_at=row.status_changed_at,
                    created_by=UserRefDTO(id=row.created_by_id, username=row.creator),
                    qr_token=row.qr_token,
                    item_count=count,
                    items_preview=preview,
                    contents_reminder_at=row.reminder_at,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
            )
        return views

    async def _contents(self, box_ids: Sequence[UUID]) -> dict[UUID, tuple[int, tuple[str, ...]]]:
        if not box_ids:
            return {}
        in_box = (
            select(
                ItemModel.box_id,
                ItemModel.name,
                func.row_number()
                .over(partition_by=ItemModel.box_id, order_by=_ITEM_ORDER)
                .label("position"),
                func.count().over(partition_by=ItemModel.box_id).label("total"),
            )
            .where(ItemModel.box_id.in_(box_ids), ItemModel.is_active)
            .subquery()
        )
        rows = await self._session.execute(
            select(in_box.c.box_id, in_box.c.name, in_box.c.total)
            .where(in_box.c.position <= PREVIEW_SIZE)
            .order_by(in_box.c.box_id, in_box.c.position)
        )
        totals: dict[UUID, int] = {}
        previews: dict[UUID, list[str]] = defaultdict(list)
        for row in rows:
            totals[row.box_id] = row.total
            previews[row.box_id].append(row.name)
        return {box_id: (total, tuple(previews[box_id])) for box_id, total in totals.items()}

    # ------------------------------------------------------------------ items

    async def items(
        self,
        project_id: ProjectId,
        *,
        states: Sequence[ItemState],
        priority: Priority | None,
        box_id: BoxId | None,
        source_room_id: RoomId | None,
        page: CursorPageRequest,
    ) -> CursorPage[ItemDTO]:
        query = _items().where(ItemModel.project_id == project_id)
        if states:
            query = query.where(ItemModel.state.in_([state.value for state in states]))
        if priority is not None:
            query = query.where(ItemModel.priority == priority.value)
        if box_id is not None:
            query = query.where(ItemModel.box_id == box_id)
        if source_room_id is not None:
            query = query.where(ItemModel.source_room_id == source_room_id)
        if page.after is not None:
            last_priority, last_name, last_id = decode_cursor(page.after, length=3)
            query = query.where(
                tuple_(*_ITEM_ORDER) > tuple_(_int(last_priority), _str(last_name), _uuid(last_id))
            )

        rows = (
            await self._session.execute(query.order_by(*_ITEM_ORDER).limit(page.limit + 1))
        ).all()
        views = [_item_view(row) for row in rows[: page.limit]]
        if len(rows) <= page.limit:
            return CursorPage(items=tuple(views), next_cursor=None)
        last = rows[page.limit - 1]
        return CursorPage(
            items=tuple(views),
            next_cursor=encode_cursor([last.priority, last.name.lower(), str(last.id)]),
        )

    async def items_by_ids(self, item_ids: Sequence[ItemId]) -> list[ItemDTO]:
        if not item_ids:
            return []
        rows = await self._session.execute(_items().where(ItemModel.id.in_(item_ids)))
        views = {row.id: _item_view(row) for row in rows}
        return [views[item_id] for item_id in item_ids if item_id in views]

    async def search(
        self, project_id: ProjectId, words: Sequence[str], *, limit: int, offset: int
    ) -> ItemSearchDTO:
        query = func.to_tsquery(_RUSSIAN, " & ".join(f"{word}:*" for word in words))
        rows = (
            await self._session.execute(
                _items()
                .where(ItemModel.project_id == project_id, _ITEM_VECTOR.bool_op("@@")(query))
                .order_by(
                    func.ts_rank_cd(_ITEM_VECTOR, query).desc(),
                    BoxModel.number.asc().nulls_last(),
                    ItemModel.id,
                )
                .limit(limit + 1)
                .offset(offset)
            )
        ).all()
        return ItemSearchDTO(
            items=tuple(_item_view(row) for row in rows[:limit]), has_more=len(rows) > limit
        )

    async def suggestions(
        self, project_id: ProjectId, source_room_id: RoomId, *, exclude_box_id: BoxId | None
    ) -> list[BoxSuggestionDTO]:
        matches = func.count().label("matches")
        query = (
            select(
                BoxModel.id,
                BoxModel.number,
                BoxModel.status,
                _DESTINATION.id.label("room_id"),
                _DESTINATION.name.label("room_name"),
                matches,
            )
            .join(ItemModel, ItemModel.box_id == BoxModel.id)
            .join(_DESTINATION, _DESTINATION.id == BoxModel.destination_room_id)
            .where(
                ItemModel.project_id == project_id,
                ItemModel.is_active,
                ItemModel.source_room_id == source_room_id,
                BoxModel.is_active,
                BoxModel.status != BoxStatus.UNPACKED.value,
            )
            .group_by(BoxModel.id, BoxModel.number, BoxModel.status, _DESTINATION.id)
            .order_by(matches.desc(), BoxModel.number)
        )
        if exclude_box_id is not None:
            query = query.where(BoxModel.id != exclude_box_id)
        rows = await self._session.execute(query)
        return [
            BoxSuggestionDTO(
                box=BoxRefDTO(
                    id=row.id,
                    number=row.number,
                    status=BoxStatus(row.status),
                    destination_room=RoomRefDTO(id=row.room_id, name=row.room_name),
                ),
                matches=row.matches,
            )
            for row in rows
        ]


def _boxes(viewer_id: UserId) -> Select[*tuple[Any, ...]]:
    """Active boxes with their room and creator. The pending contents reminder is
    shown to the creator only (it is addressed to them)."""
    reminder_at = (
        select(NotificationModel.scheduled_at)
        .where(
            NotificationModel.box_id == BoxModel.id,
            NotificationModel.type == NotificationType.BOX_CONTENTS_REMINDER.value,
            NotificationModel.status == NotificationStatus.PENDING.value,
            NotificationModel.user_id == viewer_id,
        )
        .correlate(BoxModel)
        .limit(1)
        .scalar_subquery()
    )
    return (
        select(
            BoxModel.id,
            BoxModel.number,
            BoxModel.title,
            BoxModel.is_fragile,
            BoxModel.status,
            BoxModel.status_changed_at,
            BoxModel.created_by_id,
            BoxModel.qr_token,
            BoxModel.created_at,
            BoxModel.updated_at,
            _DESTINATION.id.label("room_id"),
            _DESTINATION.name.label("room_name"),
            UserModel.username.label("creator"),
            reminder_at.label("reminder_at"),
        )
        .join(_DESTINATION, _DESTINATION.id == BoxModel.destination_room_id)
        .join(UserModel, UserModel.id == BoxModel.created_by_id)
        .where(BoxModel.is_active)
    )


def _items() -> Select[*tuple[Any, ...]]:
    """Active items with their source room and, when packed, their box."""
    return (
        select(
            ItemModel.id,
            ItemModel.name,
            ItemModel.quantity,
            ItemModel.priority,
            ItemModel.state,
            ItemModel.created_at,
            ItemModel.updated_at,
            _SOURCE.id.label("source_id"),
            _SOURCE.name.label("source_name"),
            BoxModel.id.label("box_id"),
            BoxModel.number.label("box_number"),
            BoxModel.status.label("box_status"),
            _DESTINATION.id.label("destination_id"),
            _DESTINATION.name.label("destination_name"),
        )
        .join(_SOURCE, _SOURCE.id == ItemModel.source_room_id)
        .outerjoin(BoxModel, and_(BoxModel.id == ItemModel.box_id, BoxModel.is_active))
        .outerjoin(_DESTINATION, _DESTINATION.id == BoxModel.destination_room_id)
        .where(ItemModel.is_active)
    )


def _item_view(row: Row[Any]) -> ItemDTO:
    box = (
        None
        if row.box_id is None
        else BoxRefDTO(
            id=row.box_id,
            number=row.box_number,
            status=BoxStatus(row.box_status),
            destination_room=RoomRefDTO(id=row.destination_id, name=row.destination_name),
        )
    )
    return ItemDTO(
        id=row.id,
        name=row.name,
        quantity=row.quantity,
        priority=Priority(row.priority),
        state=ItemState(row.state),
        source_room=RoomRefDTO(id=row.source_id, name=row.source_name),
        box=box,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidPaginationError("Malformed cursor")
    return value


def _str(value: object) -> str:
    if not isinstance(value, str):
        raise InvalidPaginationError("Malformed cursor")
    return value


def _uuid(value: object) -> UUID:
    try:
        return UUID(_str(value))
    except ValueError as error:
        raise InvalidPaginationError("Malformed cursor") from error
