from app.application.common.pagination import CursorPage
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.inventory.dto import ItemDTO, ListItemsDTO
from app.application.inventory.lookup import project_of_member
from app.application.inventory.read_model import AbstractInventoryReadModel
from app.domain.common.ids import BoxId, RoomId


class ListItems:
    def __init__(
        self, projects: AbstractProjectRepository, read_model: AbstractInventoryReadModel
    ) -> None:
        self._projects = projects
        self._read_model = read_model

    async def __call__(self, data: ListItemsDTO) -> CursorPage[ItemDTO]:
        """Active items in packing order (priority, name, id), filtered and paged.

        Raises:
            InvalidPaginationError: the limit is out of range or the cursor is malformed.
            ProjectNotFoundError: no such active project, or the caller is not a member.
        """
        project = await project_of_member(self._projects, data.project_id, data.user_id)
        return await self._read_model.items(
            project.id,
            states=data.states,
            priority=data.priority,
            box_id=None if data.box_id is None else BoxId(data.box_id),
            source_room_id=None if data.source_room_id is None else RoomId(data.source_room_id),
            page=data.page,
        )
