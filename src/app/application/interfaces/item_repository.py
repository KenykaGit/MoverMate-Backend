from abc import ABC, abstractmethod
from collections.abc import Sequence

from app.domain.common.ids import BoxId, ItemId
from app.domain.inventory.item import Item


class AbstractItemRepository(ABC):
    """Access to persisted items. Soft-deleted items are hidden from every lookup.

    Implementations must not commit: the unit of work owns the transaction.
    """

    @abstractmethod
    async def add_many(self, items: Sequence[Item]) -> None:
        """Stage new items, in this order."""

    @abstractmethod
    async def get(self, item_id: ItemId) -> Item | None:
        """The active item with this id, or None."""

    @abstractmethod
    async def update(self, item: Item) -> None:
        """Stage changes of an existing item (also a soft delete).

        Raises:
            ItemNotFoundError: there is no item with this id.
            ItemStateTransitionError: the stored state changed meanwhile so that the
                move is no longer allowed (the database guards the matrix too).
        """

    @abstractmethod
    async def deactivate_in_box(self, box_id: BoxId) -> int:
        """Soft-delete every active item in the box; how many there were."""
