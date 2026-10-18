import logging

from app.application.common.membership import project_of_member
from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.box_repository import AbstractBoxRepository
from app.application.interfaces.item_repository import AbstractItemRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.room_repository import AbstractRoomRepository
from app.application.inventory.dto import CreateItemDTO, ItemDTO, NewItemEntryDTO
from app.application.inventory.lookup import box_in_project, item_views, room_in_project
from app.application.inventory.read_model import AbstractInventoryReadModel
from app.application.inventory.services import build_items

logger = logging.getLogger(__name__)


class CreateItem:
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

    async def __call__(self, data: CreateItemDTO) -> ItemDTO:
        """A new item in its source room (TO_PACK), or straight into a box (IN_BOX).

        Raises:
            ProjectNotFoundError: no such active project, or the caller is not a member.
            RoomNotFoundError, BoxNotFoundError: no such active room or box in the project.
            WrongRoomKindError: the source is not a SOURCE room.
            InvalidItemNameError, InvalidQuantityError: bad values.
        """
        project = await project_of_member(self._projects, data.project_id, data.user_id)
        source = await room_in_project(self._rooms, data.source_room_id, project.id)
        box = (
            None
            if data.box_id is None
            else await box_in_project(self._boxes, data.box_id, project.id)
        )
        (item,) = build_items(
            project_id=project.id,
            source=source,
            entries=[NewItemEntryDTO(name=data.name, quantity=data.quantity)],
            priority=data.priority,
            box=box,
        )

        await self._items.add_many([item])
        (view,) = await item_views(self._read_model, [item.id])
        await self._uow.commit()

        logger.info(
            "item_created",
            extra={
                "project_id": str(project.id),
                "item_id": str(item.id),
                "item_name": item.name.value,
                "quantity": item.quantity.value,
                "in_box": box is not None,
                "user_id": str(data.user_id),
            },
        )
        return view
