from app.application.interfaces.item_repository import AbstractItemRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.inventory.dto import BoxSuggestionDTO, SuggestBoxesDTO
from app.application.inventory.lookup import item_of_member
from app.application.inventory.read_model import AbstractInventoryReadModel


class SuggestBoxes:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        items: AbstractItemRepository,
        read_model: AbstractInventoryReadModel,
    ) -> None:
        self._projects = projects
        self._items = items
        self._read_model = read_model

    async def __call__(self, data: SuggestBoxesDTO) -> list[BoxSuggestionDTO]:
        """Where a lost item may be: boxes not yet unpacked holding other items from its
        source room, most matches first. The item's own box, if any, is left out.

        Raises:
            ItemNotFoundError: no such active item in a project of the caller.
        """
        item = await item_of_member(self._items, self._projects, data.item_id, data.user_id)
        return await self._read_model.suggestions(
            item.project_id, item.source_room_id, exclude_box_id=item.box_id
        )
