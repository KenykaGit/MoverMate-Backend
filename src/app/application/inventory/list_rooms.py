from app.application.common.membership import project_of_member
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.room_repository import AbstractRoomRepository
from app.application.inventory.dto import ListRoomsDTO, RoomDTO


class ListRooms:
    def __init__(self, projects: AbstractProjectRepository, rooms: AbstractRoomRepository) -> None:
        self._projects = projects
        self._rooms = rooms

    async def __call__(self, data: ListRoomsDTO) -> list[RoomDTO]:
        """Active rooms of the project, of one kind or both, by name.

        Raises:
            ProjectNotFoundError: no such active project, or the caller is not a member.
        """
        project = await project_of_member(self._projects, data.project_id, data.user_id)
        rooms = await self._rooms.list(project.id, data.kind)
        return [RoomDTO.from_entity(room) for room in rooms]
