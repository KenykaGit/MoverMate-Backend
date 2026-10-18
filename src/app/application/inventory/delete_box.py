import logging

from app.application.common.clock import utc_now
from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.audit_log_repository import AbstractAuditLogRepository
from app.application.interfaces.box_repository import AbstractBoxRepository
from app.application.interfaces.item_repository import AbstractItemRepository
from app.application.interfaces.notification_repository import AbstractNotificationRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.inventory.dto import DeleteBoxDTO
from app.application.inventory.lookup import box_of_member
from app.domain.audit.entity import AuditAction, AuditEntry
from app.domain.common.ids import UserId

logger = logging.getLogger(__name__)


class DeleteBox:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        boxes: AbstractBoxRepository,
        items: AbstractItemRepository,
        notifications: AbstractNotificationRepository,
        audit_log: AbstractAuditLogRepository,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._boxes = boxes
        self._items = items
        self._notifications = notifications
        self._audit_log = audit_log
        self._uow = uow

    async def __call__(self, data: DeleteBoxDTO) -> None:
        """Soft-delete a box together with the items in it; its number becomes free.

        Cancels its pending notifications and records BOX_DELETED.

        Raises:
            BoxNotFoundError: no such active box in a project of the caller.
        """
        box = await box_of_member(self._boxes, self._projects, data.box_id, data.user_id)

        box.soft_delete()
        await self._boxes.update(box)
        deleted_items = await self._items.deactivate_in_box(box.id)
        await self._notifications.cancel_pending_for_box(box.id)
        await self._audit_log.add(
            AuditEntry.record(
                project_id=box.project_id,
                actor_id=UserId(data.user_id),
                action=AuditAction.BOX_DELETED,
                entity_id=box.id,
                now=utc_now(),
                details={"number": str(box.number), "items": str(deleted_items)},
            )
        )
        await self._uow.commit()

        logger.info(
            "box_deleted",
            extra={
                "project_id": str(box.project_id),
                "box_id": str(box.id),
                "number": box.number.value,
                "items": deleted_items,
                "user_id": str(data.user_id),
            },
        )
