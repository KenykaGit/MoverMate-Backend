import logging

from app.application.common.uow import AbstractUnitOfWork
from app.application.exceptions import RoomInUseError
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.room_repository import AbstractRoomRepository
from app.application.inventory.dto import DeleteRoomDTO
from app.application.inventory.lookup import room_of_member

logger = logging.getLogger(__name__)


class DeleteRoom:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        rooms: AbstractRoomRepository,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._rooms = rooms
        self._uow = uow

    async def __call__(self, data: DeleteRoomDTO) -> None:
        """Soft-delete a room that nothing refers to any more.

        Raises:
            RoomNotFoundError: no such active room in a project of the caller.
            RoomInUseError: active boxes or items still refer to the room.
        """
        room = await room_of_member(self._rooms, self._projects, data.room_id, data.user_id)
        if await self._rooms.is_referenced(room.id):
            raise RoomInUseError(f"Room {room.id} still has active boxes or items")

        room.soft_delete()
        await self._rooms.update(room)
        await self._uow.commit()

        logger.info(
            "room_deleted",
            extra={
                "project_id": str(room.project_id),
                "room_id": str(room.id),
                "room_name": room.name.value,
                "user_id": str(data.user_id),
            },
        )
