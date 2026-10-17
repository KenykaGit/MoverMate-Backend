from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.exceptions import RoomNameTakenError, RoomNotFoundError
from app.application.interfaces.room_repository import AbstractRoomRepository
from app.domain.common.ids import ProjectId, RoomId
from app.domain.inventory.room import Room
from app.domain.inventory.value_objects import RoomKind
from app.infrastructure.db.errors import violated_constraint
from app.infrastructure.db.mappers.inventory import RoomMapper
from app.infrastructure.db.models.box import BoxModel
from app.infrastructure.db.models.item import ItemModel
from app.infrastructure.db.models.room import ACTIVE_ROOM_NAME_UNIQUE_INDEX, RoomModel


class SqlAlchemyRoomRepository(AbstractRoomRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, room: Room) -> None:
        async with self._flush_or_raise_conflict(room):
            self._session.add(RoomMapper.to_model(room))

    async def get(self, room_id: RoomId) -> Room | None:
        model = await self._session.get(RoomModel, room_id)
        if model is None or not model.is_active:
            return None
        return RoomMapper.to_domain(model)

    async def list(self, project_id: ProjectId, kind: RoomKind | None = None) -> list[Room]:
        query = select(RoomModel).where(RoomModel.project_id == project_id, RoomModel.is_active)
        if kind is not None:
            query = query.where(RoomModel.kind == kind.value)
        models = await self._session.scalars(query.order_by(RoomModel.name, RoomModel.id))
        return [RoomMapper.to_domain(model) for model in models]

    async def update(self, room: Room) -> None:
        # merge() alone would insert a missing room, so check existence first.
        if await self._session.get(RoomModel, room.id) is None:
            raise RoomNotFoundError(room.id)
        async with self._flush_or_raise_conflict(room):
            await self._session.merge(RoomMapper.to_model(room))

    async def is_referenced(self, room_id: RoomId) -> bool:
        box = exists().where(BoxModel.destination_room_id == room_id, BoxModel.is_active)
        item = exists().where(ItemModel.source_room_id == room_id, ItemModel.is_active)
        return bool(await self._session.scalar(select(or_(box, item))))

    @asynccontextmanager
    async def _flush_or_raise_conflict(self, room: Room) -> AsyncIterator[None]:
        """Flushed inside a SAVEPOINT, like SqlAlchemyUserRepository: the error names
        the taken name, and the rest of the transaction stays usable."""
        try:
            async with self._session.begin_nested():
                yield
        except IntegrityError as error:
            if violated_constraint(error) == ACTIVE_ROOM_NAME_UNIQUE_INDEX:
                raise RoomNameTakenError(room.kind, room.name) from error
            raise
