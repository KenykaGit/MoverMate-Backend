import logging

from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.item_repository import AbstractItemRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.inventory.dto import DeleteItemDTO
from app.application.inventory.lookup import item_of_member

logger = logging.getLogger(__name__)


class DeleteItem:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        items: AbstractItemRepository,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._items = items
        self._uow = uow

    async def __call__(self, data: DeleteItemDTO) -> None:
        """Soft-delete an item.

        Raises:
            ItemNotFoundError: no such active item in a project of the caller.
        """
        item = await item_of_member(self._items, self._projects, data.item_id, data.user_id)

        item.soft_delete()
        await self._items.update(item)
        await self._uow.commit()

        logger.info(
            "item_deleted",
            extra={
                "project_id": str(item.project_id),
                "item_id": str(item.id),
                "item_name": item.name.value,
                "user_id": str(data.user_id),
            },
        )
