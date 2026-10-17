from app.application.common.pagination import CursorPage
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.inventory.dto import BoxDTO, ListBoxesDTO
from app.application.inventory.lookup import project_of_member
from app.application.inventory.read_model import AbstractInventoryReadModel
from app.domain.common.ids import RoomId, UserId


class ListBoxes:
    def __init__(
        self, projects: AbstractProjectRepository, read_model: AbstractInventoryReadModel
    ) -> None:
        self._projects = projects
        self._read_model = read_model

    async def __call__(self, data: ListBoxesDTO) -> CursorPage[BoxDTO]:
        """Active boxes of the project by number, filtered and paged by cursor.

        Raises:
            InvalidPaginationError: the limit is out of range or the cursor is malformed.
            ProjectNotFoundError: no such active project, or the caller is not a member.
        """
        project = await project_of_member(self._projects, data.project_id, data.user_id)
        room_id = data.destination_room_id
        return await self._read_model.boxes(
            project.id,
            viewer_id=UserId(data.user_id),
            statuses=data.statuses,
            destination_room_id=None if room_id is None else RoomId(room_id),
            number=data.number,
            page=data.page,
        )
