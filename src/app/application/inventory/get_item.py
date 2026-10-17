from app.application.interfaces.item_repository import AbstractItemRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.inventory.dto import GetItemDTO, ItemDTO
from app.application.inventory.lookup import item_of_member, item_views
from app.application.inventory.read_model import AbstractInventoryReadModel


class GetItem:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        items: AbstractItemRepository,
        read_model: AbstractInventoryReadModel,
    ) -> None:
        self._projects = projects
        self._items = items
        self._read_model = read_model

    async def __call__(self, data: GetItemDTO) -> ItemDTO:
        """The active item with its source room and box.

        Raises:
            ItemNotFoundError: no such active item in a project of the caller.
        """
        item = await item_of_member(self._items, self._projects, data.item_id, data.user_id)
        (view,) = await item_views(self._read_model, [item.id])
        return view
