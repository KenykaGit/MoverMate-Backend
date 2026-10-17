import logging

from app.application.common.clock import utc_now
from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.audit_log_repository import AbstractAuditLogRepository
from app.application.interfaces.box_repository import AbstractBoxRepository
from app.application.interfaces.notification_repository import AbstractNotificationRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.inventory.dto import BoxDTO, ChangeBoxStatusDTO
from app.application.inventory.lookup import box_of_member, box_view
from app.application.inventory.read_model import AbstractInventoryReadModel
from app.domain.audit.entity import AuditAction, AuditEntry
from app.domain.common.ids import UserId
from app.domain.inventory.box import BoxStatus

logger = logging.getLogger(__name__)


class ChangeBoxStatus:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        boxes: AbstractBoxRepository,
        notifications: AbstractNotificationRepository,
        audit_log: AbstractAuditLogRepository,
        read_model: AbstractInventoryReadModel,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._boxes = boxes
        self._notifications = notifications
        self._audit_log = audit_log
        self._read_model = read_model
        self._uow = uow

    async def __call__(self, data: ChangeBoxStatusDTO) -> BoxDTO:
        """Move a box one step forward or back; the current status again changes nothing.

        Records BOX_STATUS_CHANGED or BOX_STATUS_ROLLED_BACK in the audit log. Leaving
        PACKING cancels the pending contents reminder.

        Raises:
            BoxNotFoundError: no such active box in a project of the caller.
            BoxStatusTransitionError: the move skips a step.
            BoxStatusConflictError: the box changed status concurrently.
        """
        box = await box_of_member(self._boxes, self._projects, data.box_id, data.user_id)
        now = utc_now()
        stored = box.status

        change = box.change_status(data.status, now=now)
        if change is not None:
            await self._boxes.change_status(box, expected=stored)
            if change.from_status is BoxStatus.PACKING:
                await self._notifications.cancel_pending_for_box(box.id)
            action = (
                AuditAction.BOX_STATUS_ROLLED_BACK
                if change.is_rollback
                else AuditAction.BOX_STATUS_CHANGED
            )
            await self._audit_log.add(
                AuditEntry.record(
                    project_id=box.project_id,
                    actor_id=UserId(data.user_id),
                    action=action,
                    entity_id=box.id,
                    now=now,
                    details={"from": change.from_status, "to": change.to_status},
                )
            )

        view = await box_view(self._read_model, box.id, data.user_id)
        await self._uow.commit()

        if change is not None:
            logger.info(
                "box_status_changed",
                extra={
                    "box_id": str(box.id),
                    "from_status": change.from_status,
                    "to_status": change.to_status,
                    "rollback": change.is_rollback,
                },
            )
        return view
