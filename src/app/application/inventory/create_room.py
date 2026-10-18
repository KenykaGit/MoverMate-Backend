import logging

from app.application.common.membership import project_of_member
from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.room_repository import AbstractRoomRepository
from app.application.inventory.dto import CreateRoomDTO, RoomDTO
from app.domain.inventory.room import Room
from app.domain.inventory.value_objects import RoomName

logger = logging.getLogger(__name__)


class CreateRoom:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        rooms: AbstractRoomRepository,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._rooms = rooms
        self._uow = uow

    async def __call__(self, data: CreateRoomDTO) -> RoomDTO:
        """Add a room of the old home (SOURCE) or of the new one (DESTINATION).

        Raises:
            InvalidRoomNameError: the name is empty or too long.
            ProjectNotFoundError: no such active project, or the caller is not a member.
            RoomNameTakenError: an active room of this kind already has the name.
        """
        name = RoomName(data.name)
        project = await project_of_member(self._projects, data.project_id, data.user_id)

        room = Room.create(project_id=project.id, kind=data.kind, name=name)
        await self._rooms.add(room)
        await self._uow.commit()

        logger.info(
            "room_created",
            extra={
                "project_id": str(project.id),
                "room_id": str(room.id),
                "room_name": room.name.value,
                "room_kind": room.kind,
                "user_id": str(data.user_id),
            },
        )
        return RoomDTO.from_entity(room)
