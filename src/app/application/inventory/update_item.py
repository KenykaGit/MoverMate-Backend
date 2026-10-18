import logging

from app.application.common.patch import UNSET, sent_fields
from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.item_repository import AbstractItemRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.room_repository import AbstractRoomRepository
from app.application.inventory.dto import ItemDTO, UpdateItemDTO
from app.application.inventory.lookup import item_of_member, item_views, room_in_project
from app.application.inventory.read_model import AbstractInventoryReadModel
from app.domain.inventory.value_objects import ItemName, Quantity

logger = logging.getLogger(__name__)


class UpdateItem:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        rooms: AbstractRoomRepository,
        items: AbstractItemRepository,
        read_model: AbstractInventoryReadModel,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._rooms = rooms
        self._items = items
        self._read_model = read_model
        self._uow = uow

    async def __call__(self, data: UpdateItemDTO) -> ItemDTO:
        """Change the name, quantity, priority or source room of an item.

        Raises:
            ItemNotFoundError: no such active item in a project of the caller.
            RoomNotFoundError: no such active room in the project.
            WrongRoomKindError: the new source is not a SOURCE room.
            InvalidItemNameError, InvalidQuantityError: bad values.
        """
        name = None if data.name is UNSET else ItemName(data.name)
        quantity = None if data.quantity is UNSET else Quantity(data.quantity)
        item = await item_of_member(self._items, self._projects, data.item_id, data.user_id)

        if name is not None:
            item.rename(name)
        if quantity is not None:
            item.change_quantity(quantity)
        if data.priority is not UNSET:
            item.set_priority(data.priority)
        if data.source_room_id is not UNSET:
            source = await room_in_project(self._rooms, data.source_room_id, item.project_id)
            item.change_source_room(source)

        await self._items.update(item)
        (view,) = await item_views(self._read_model, [item.id])
        await self._uow.commit()

        fields = sent_fields(data, "name", "quantity", "priority", "source_room_id")
        if fields:
            logger.info(
                "item_updated",
                extra={
                    "project_id": str(item.project_id),
                    "item_id": str(item.id),
                    "item_name": item.name.value,
                    "user_id": str(data.user_id),
                    "fields": fields,
                },
            )
        return view
