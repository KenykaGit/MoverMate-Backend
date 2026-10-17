from collections.abc import Sequence

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.exceptions import ItemNotFoundError
from app.application.interfaces.item_repository import AbstractItemRepository
from app.domain.common.ids import BoxId, ItemId
from app.domain.inventory.errors import ItemStateTransitionError
from app.domain.inventory.item import Item
from app.infrastructure.db.errors import violated_constraint
from app.infrastructure.db.mappers.inventory import ItemMapper
from app.infrastructure.db.models.item import ITEM_STATE_TRANSITION_CONSTRAINT, ItemModel


class SqlAlchemyItemRepository(AbstractItemRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_many(self, items: Sequence[Item]) -> None:
        self._session.add_all([ItemMapper.to_model(item) for item in items])
        await self._session.flush()

    async def get(self, item_id: ItemId) -> Item | None:
        model = await self._session.get(ItemModel, item_id)
        if model is None or not model.is_active:
            return None
        return ItemMapper.to_domain(model)

    async def update(self, item: Item) -> None:
        # merge() alone would insert a missing item, so check existence first.
        if await self._session.get(ItemModel, item.id) is None:
            raise ItemNotFoundError(item.id)
        try:
            async with self._session.begin_nested():
                await self._session.merge(ItemMapper.to_model(item))
        except IntegrityError as error:
            # The trigger compares with the stored state: a concurrent change may
            # have made this move one the matrix does not allow.
            if violated_constraint(error) == ITEM_STATE_TRANSITION_CONSTRAINT:
                raise ItemStateTransitionError(
                    f"Item {item.id} changed meanwhile; it cannot go to {item.state} now"
                ) from error
            raise

    async def deactivate_in_box(self, box_id: BoxId) -> int:
        result = await self._session.execute(
            update(ItemModel)
            .where(ItemModel.box_id == box_id, ItemModel.is_active)
            .values(is_active=False)
            .returning(ItemModel.id)
        )
        return len(result.all())
