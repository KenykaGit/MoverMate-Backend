from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.room_repository import AbstractRoomRepository
from app.application.inventory.dto import RenameRoomDTO, RoomDTO
from app.application.inventory.lookup import room_of_member
from app.domain.inventory.value_objects import RoomName


class RenameRoom:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        rooms: AbstractRoomRepository,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._rooms = rooms
        self._uow = uow

    async def __call__(self, data: RenameRoomDTO) -> RoomDTO:
        """Rename a room; its kind never changes.

        Raises:
            InvalidRoomNameError: the name is empty or too long.
            RoomNotFoundError: no such active room in a project of the caller.
            RoomNameTakenError: an active room of this kind already has the name.
        """
        name = RoomName(data.name)
        room = await room_of_member(self._rooms, self._projects, data.room_id, data.user_id)

        room.rename(name)
        await self._rooms.update(room)
        await self._uow.commit()
        return RoomDTO.from_entity(room)
