from collections import defaultdict
from collections.abc import Collection
from typing import Any
from uuid import UUID

from sqlalchemy import Row, func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.application.inventory.dto import BoxRefDTO, RoomRefDTO
from app.application.summaries.dto import (
    BoxLabelDTO,
    DoneCountDTO,
    LoadingChecklistRoomDTO,
    ProjectSummaryDTO,
    UnpackingProgressRoomDTO,
)
from app.application.summaries.read_model import AbstractSummaryReadModel
from app.application.tasks.dto import TaskDTO
from app.domain.common.ids import BoxId, ProjectId
from app.domain.inventory.box import BoxStatus
from app.domain.inventory.item import ItemState
from app.domain.inventory.value_objects import Priority, RoomKind
from app.domain.tasks.value_objects import TaskPhase
from app.infrastructure.db.models.box import BoxModel
from app.infrastructure.db.models.item import ItemModel
from app.infrastructure.db.models.room import RoomModel
from app.infrastructure.db.models.task import TaskModel
from app.infrastructure.db.queries.tasks import load_task_views

type _Row = Row[*tuple[Any, ...]]

_B = BoxModel
_I = ItemModel
_R = RoomModel
_T = TaskModel
_DESTINATION = aliased(RoomModel, name="destination_room")

# Packing order, as in the inventory read model: priority, then name.
_ITEM_ORDER = (_I.priority, func.lower(_I.name), _I.id)


class SqlAlchemySummaryReadModel(AbstractSummaryReadModel):
    """Aggregates in SQL, one statement per answer where consistency matters: a
    statement sees one snapshot, so its numbers never contradict each other."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def project_summary(self, project_id: ProjectId) -> ProjectSummaryDTO:
        # One statement: boxes, items and tasks are counted at the same moment.
        counts = union_all(
            select(
                literal("box").label("kind"),
                _B.status.label("key"),
                func.count().label("total"),
                literal(0).label("extra"),
            )
            .where(_B.project_id == project_id, _B.is_active)
            .group_by(_B.status),
            select(
                literal("item"),
                _I.state,
                func.count(),
                func.count().filter(_I.priority == Priority.FIRST_NECESSITY.value),
            )
            .where(_I.project_id == project_id, _I.is_active)
            .group_by(_I.state),
            select(literal("task"), _T.phase, func.count(), func.count().filter(_T.is_done))
            .where(_T.project_id == project_id, _T.is_active)
            .group_by(_T.phase),
        )
        found: dict[tuple[str, str], tuple[int, int]] = {
            (row.kind, row.key): (row.total, row.extra)
            for row in await self._session.execute(counts)
        }

        def total(kind: str, key: str) -> int:
            return found.get((kind, key), (0, 0))[0]

        def extra(kind: str, key: str) -> int:
            return found.get((kind, key), (0, 0))[1]

        return ProjectSummaryDTO(
            boxes_by_status={status: total("box", status) for status in BoxStatus},
            items_by_state={state: total("item", state) for state in ItemState},
            tasks_by_phase={
                phase: DoneCountDTO(total=total("task", phase), done=extra("task", phase))
                for phase in TaskPhase
            },
            first_necessity=DoneCountDTO(
                total=sum(extra("item", state) for state in ItemState),
                done=extra("item", ItemState.TAKEN_OUT),
            ),
            next_task=await self._next_task(project_id),
        )

    async def loading_checklist(self, project_id: ProjectId) -> list[LoadingChecklistRoomDTO]:
        # One statement: "nothing left to pack" and "every box loaded" must describe
        # the same moment, or an item moving between them could be missed.
        rows = await self._session.execute(
            select(
                _R.id.label("room_id"),
                _R.name.label("room_name"),
                _B.id.label("box_id"),
                _B.number,
                _B.status,
                _DESTINATION.id.label("destination_id"),
                _DESTINATION.name.label("destination_name"),
                func.count(_I.id).filter(_I.state == ItemState.TO_PACK).label("to_pack"),
            )
            .select_from(_R)
            .outerjoin(
                _I,
                (_I.source_room_id == _R.id)
                & _I.is_active
                & _I.state.in_([ItemState.TO_PACK, ItemState.IN_BOX]),
            )
            .outerjoin(_B, (_B.id == _I.box_id) & _B.is_active)
            .outerjoin(_DESTINATION, _DESTINATION.id == _B.destination_room_id)
            .where(_R.project_id == project_id, _R.kind == RoomKind.SOURCE, _R.is_active)
            # A group per (room, box); items waiting to be packed form the (room, NULL) group.
            .group_by(
                _R.id, _R.name, _B.id, _B.number, _B.status, _DESTINATION.id, _DESTINATION.name
            )
            .order_by(_R.name, _R.id, _B.number)
        )
        rooms: dict[UUID, RoomRefDTO] = {}
        to_pack: defaultdict[UUID, int] = defaultdict(int)
        boxes: defaultdict[UUID, list[BoxRefDTO]] = defaultdict(list)
        for row in rows:
            rooms.setdefault(row.room_id, RoomRefDTO(id=row.room_id, name=row.room_name))
            to_pack[row.room_id] += row.to_pack
            if row.box_id is not None:
                boxes[row.room_id].append(_box_ref(row))
        return [
            LoadingChecklistRoomDTO(
                room=room, to_pack_count=to_pack[room_id], boxes=tuple(boxes[room_id])
            )
            for room_id, room in rooms.items()
        ]

    async def unpacking_progress(self, project_id: ProjectId) -> list[UnpackingProgressRoomDTO]:
        rows = await self._session.execute(
            select(
                _R.id,
                _R.name,
                func.count(_B.id).label("total"),
                func.count(_B.id).filter(_B.status == BoxStatus.UNPACKED).label("unpacked"),
            )
            .select_from(_R)
            .outerjoin(_B, (_B.destination_room_id == _R.id) & _B.is_active)
            .where(_R.project_id == project_id, _R.kind == RoomKind.DESTINATION, _R.is_active)
            .group_by(_R.id, _R.name)
            .order_by(_R.name, _R.id)
        )
        return [
            UnpackingProgressRoomDTO(
                room=RoomRefDTO(id=row.id, name=row.name),
                total=row.total,
                unpacked=row.unpacked,
            )
            for row in rows
        ]

    async def box_labels(
        self, project_id: ProjectId, box_ids: Collection[BoxId] | None = None
    ) -> list[BoxLabelDTO]:
        item_names = (
            func.array_agg(_I.name)
            .aggregate_order_by(*_ITEM_ORDER)
            .filter(_I.id.is_not(None))  # a box without items: NULL, not [NULL]
        )
        query = (
            select(
                _B.id,
                _B.number,
                _B.title,
                _B.is_fragile,
                _B.qr_token,
                _DESTINATION.id.label("destination_id"),
                _DESTINATION.name.label("destination_name"),
                item_names.label("item_names"),
            )
            .join(_DESTINATION, _DESTINATION.id == _B.destination_room_id)
            .outerjoin(_I, (_I.box_id == _B.id) & _I.is_active)
            .where(_B.project_id == project_id, _B.is_active)
            # Primary keys: the other columns of both tables depend on them.
            .group_by(_B.id, _DESTINATION.id)
            .order_by(_B.number)
        )
        if box_ids is not None:
            query = query.where(_B.id.in_(box_ids))
        return [
            BoxLabelDTO(
                id=row.id,
                number=row.number,
                title=row.title,
                is_fragile=row.is_fragile,
                destination_room=RoomRefDTO(id=row.destination_id, name=row.destination_name),
                qr_token=row.qr_token,
                item_names=tuple(row.item_names or ()),
            )
            for row in await self._session.execute(query)
        ]

    async def _next_task(self, project_id: ProjectId) -> TaskDTO | None:
        views = await load_task_views(
            self._session, _T.project_id == project_id, ~_T.is_done, limit=1
        )
        return views[0] if views else None


def _box_ref(row: _Row) -> BoxRefDTO:
    return BoxRefDTO(
        id=row.box_id,
        number=row.number,
        status=BoxStatus(row.status),
        destination_room=RoomRefDTO(id=row.destination_id, name=row.destination_name),
    )
