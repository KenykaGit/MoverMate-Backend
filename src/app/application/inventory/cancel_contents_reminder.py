import logging

from app.application.common.uow import AbstractUnitOfWork
from app.application.exceptions import NotBoxCreatorError
from app.application.interfaces.box_repository import AbstractBoxRepository
from app.application.interfaces.notification_repository import AbstractNotificationRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.inventory.dto import CancelContentsReminderDTO
from app.application.inventory.lookup import box_of_member

logger = logging.getLogger(__name__)


class CancelContentsReminder:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        boxes: AbstractBoxRepository,
        notifications: AbstractNotificationRepository,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._boxes = boxes
        self._notifications = notifications
        self._uow = uow

    async def __call__(self, data: CancelContentsReminderDTO) -> None:
        """Cancel the pending contents reminder of a box. Idempotent.

        Raises:
            BoxNotFoundError: no such active box in a project of the caller.
            NotBoxCreatorError: the caller did not create the box.
        """
        box = await box_of_member(self._boxes, self._projects, data.box_id, data.user_id)
        if box.created_by != data.user_id:
            raise NotBoxCreatorError("Only the creator of the box cancels its contents reminder")

        cancelled = await self._notifications.cancel_pending_for_box(box.id)
        await self._uow.commit()

        if cancelled:
            logger.info(
                "contents_reminder_cancelled",
                extra={
                    "project_id": str(box.project_id),
                    "box_id": str(box.id),
                    "number": box.number.value,
                    "user_id": str(data.user_id),
                },
            )
