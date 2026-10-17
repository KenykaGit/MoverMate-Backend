from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.exceptions import RoomNameTakenError
from app.application.interfaces.room_repository import AbstractRoomRepository
from app.domain.inventory.room import Room
from app.infrastructure.db.errors import violated_constraint
from app.infrastructure.db.mappers.inventory import RoomMapper
from app.infrastructure.db.models.room import ACTIVE_ROOM_NAME_UNIQUE_INDEX


class SqlAlchemyRoomRepository(AbstractRoomRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, room: Room) -> None:
        # Flushed inside a SAVEPOINT, like SqlAlchemyUserRepository: the error names
        # the taken name, and the rest of the transaction stays usable.
        try:
            async with self._session.begin_nested():
                self._session.add(RoomMapper.to_model(room))
        except IntegrityError as error:
            if violated_constraint(error) == ACTIVE_ROOM_NAME_UNIQUE_INDEX:
                raise RoomNameTakenError(room.kind, room.name) from error
            raise
