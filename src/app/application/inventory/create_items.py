import logging

from app.application.common.membership import project_of_member
from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.box_repository import AbstractBoxRepository
from app.application.interfaces.item_repository import AbstractItemRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.room_repository import AbstractRoomRepository
from app.application.inventory.dto import CreateItemsDTO, ItemDTO
from app.application.inventory.lookup import box_in_project, item_views, room_in_project
from app.application.inventory.read_model import AbstractInventoryReadModel
from app.application.inventory.services import build_items

logger = logging.getLogger(__name__)


class CreateItems:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        rooms: AbstractRoomRepository,
        boxes: AbstractBoxRepository,
        items: AbstractItemRepository,
        read_model: AbstractInventoryReadModel,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._rooms = rooms
        self._boxes = boxes
        self._items = items
        self._read_model = read_model
        self._uow = uow

    async def __call__(self, data: CreateItemsDTO) -> list[ItemDTO]:
        """Several items from one room at once: all of them or none.

        Raises:
            ProjectNotFoundError: no such active project, or the caller is not a member.
            RoomNotFoundError, BoxNotFoundError: no such active room or box in the project.
            WrongRoomKindError: the source is not a SOURCE room.
            InvalidItemNameError, InvalidQuantityError: a bad entry; nothing is created.
        """
        project = await project_of_member(self._projects, data.project_id, data.user_id)
        source = await room_in_project(self._rooms, data.items.source_room_id, project.id)
        box = (
            None
            if data.box_id is None
            else await box_in_project(self._boxes, data.box_id, project.id)
        )
        items = build_items(
            project_id=project.id,
            source=source,
            entries=data.items.entries,
            priority=data.items.priority,
            box=box,
        )

        await self._items.add_many(items)
        views = await item_views(self._read_model, [item.id for item in items])
        await self._uow.commit()

        logger.info(
            "items_created",
            extra={
                "project_id": str(project.id),
                "items": len(items),
                "item_names": [item.name.value for item in items],
                "in_box": box is not None,
                "user_id": str(data.user_id),
            },
        )
        return views
