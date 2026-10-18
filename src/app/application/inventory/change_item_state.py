import logging
from uuid import UUID

from app.application.common.clock import utc_now
from app.application.common.uow import AbstractUnitOfWork
from app.application.exceptions import ItemBoxMismatchError
from app.application.interfaces.audit_log_repository import AbstractAuditLogRepository
from app.application.interfaces.box_repository import AbstractBoxRepository
from app.application.interfaces.item_repository import AbstractItemRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.inventory.dto import ChangeItemStateDTO, ItemDTO
from app.application.inventory.lookup import box_in_project, item_of_member, item_views
from app.application.inventory.read_model import AbstractInventoryReadModel
from app.domain.audit.entity import AuditAction, AuditEntry
from app.domain.common.ids import UserId
from app.domain.inventory.item import Item, ItemState, ItemStateChange

logger = logging.getLogger(__name__)


class ChangeItemState:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        boxes: AbstractBoxRepository,
        items: AbstractItemRepository,
        audit_log: AbstractAuditLogRepository,
        read_model: AbstractInventoryReadModel,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._boxes = boxes
        self._items = items
        self._audit_log = audit_log
        self._read_model = read_model
        self._uow = uow

    async def __call__(self, data: ChangeItemStateDTO) -> ItemDTO:
        """Pack, take out, lose or find an item; the current state again changes nothing.

        Records ITEM_MARKED_LOST and ITEM_RETURNED in the audit log.

        Raises:
            ItemBoxMismatchError: box_id is missing for IN_BOX or given for another state.
            ItemNotFoundError, BoxNotFoundError: no such active item or box.
            ItemStateTransitionError: the item state matrix does not allow the move.
        """
        if (data.state is ItemState.IN_BOX) != (data.box_id is not None):
            raise ItemBoxMismatchError("box_id is required for IN_BOX and only for it")
        item = await item_of_member(self._items, self._projects, data.item_id, data.user_id)

        change = await self._move(item, data)
        if change is not None:
            await self._items.update(item)
            await self._record(item, change, data.user_id)

        (view,) = await item_views(self._read_model, [item.id])
        await self._uow.commit()

        if change is not None:
            logger.info(
                "item_state_changed",
                extra={
                    "project_id": str(item.project_id),
                    "item_id": str(item.id),
                    "item_name": item.name.value,
                    "user_id": str(data.user_id),
                    "from_state": change.from_state,
                    "to_state": change.to_state,
                },
            )
        return view

    async def _move(self, item: Item, data: ChangeItemStateDTO) -> ItemStateChange | None:
        match data.state:
            case ItemState.IN_BOX if data.box_id is not None:
                box = await box_in_project(self._boxes, data.box_id, item.project_id)
                return item.put_into(box)
            case ItemState.TAKEN_OUT:
                return item.take_out()
            case ItemState.TO_PACK:
                return item.return_to_room()
            case ItemState.LOST:
                return item.mark_lost()
        raise ItemBoxMismatchError("box_id is required for IN_BOX")  # pragma: no cover

    async def _record(self, item: Item, change: ItemStateChange, user_id: UUID) -> None:
        if change.is_loss:
            action = AuditAction.ITEM_MARKED_LOST
        elif change.is_find:
            action = AuditAction.ITEM_RETURNED
        else:
            return
        await self._audit_log.add(
            AuditEntry.record(
                project_id=item.project_id,
                actor_id=UserId(user_id),
                action=action,
                entity_id=item.id,
                now=utc_now(),
                details={"from": change.from_state, "to": change.to_state},
            )
        )
