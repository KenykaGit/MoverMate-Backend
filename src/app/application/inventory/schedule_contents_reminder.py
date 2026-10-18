import logging

from app.application.common.clock import utc_now
from app.application.common.uow import AbstractUnitOfWork
from app.application.exceptions import (
    BoxNotPackingError,
    NotBoxCreatorError,
    ReminderInThePastError,
)
from app.application.interfaces.box_repository import AbstractBoxRepository
from app.application.interfaces.notification_repository import AbstractNotificationRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.inventory.dto import BoxDTO, ScheduleContentsReminderDTO
from app.application.inventory.lookup import box_of_member, box_view
from app.application.inventory.read_model import AbstractInventoryReadModel
from app.domain.common.ids import UserId
from app.domain.inventory.box import BoxStatus
from app.domain.notifications.notification import BoxContentsReminder, Notification

logger = logging.getLogger(__name__)


class ScheduleContentsReminder:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        boxes: AbstractBoxRepository,
        notifications: AbstractNotificationRepository,
        read_model: AbstractInventoryReadModel,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._boxes = boxes
        self._notifications = notifications
        self._read_model = read_model
        self._uow = uow

    async def __call__(self, data: ScheduleContentsReminderDTO) -> BoxDTO:
        """Remind the creator of a box to fill in its contents; replaces a pending one.

        Raises:
            BoxNotFoundError: no such active box in a project of the caller.
            NotBoxCreatorError: the caller did not create the box.
            BoxNotPackingError: the box is no longer being packed.
            ReminderInThePastError: the moment has already passed.
        """
        box = await box_of_member(self._boxes, self._projects, data.box_id, data.user_id)
        if box.created_by != data.user_id:
            raise NotBoxCreatorError("Only the creator of the box sets its contents reminder")
        if box.status is not BoxStatus.PACKING:
            raise BoxNotPackingError(f"Box {box.id} is {box.status}, not PACKING")
        reminder = Notification.schedule(
            project_id=box.project_id,
            user_id=UserId(data.user_id),
            target=BoxContentsReminder(box.id),
            at=data.remind_at,
            now=utc_now(),
        )
        if reminder is None:
            raise ReminderInThePastError("The moment of the reminder has already passed")

        await self._notifications.cancel_pending_for_box(box.id)
        await self._notifications.add(reminder)
        view = await box_view(self._read_model, box.id, data.user_id)
        await self._uow.commit()

        logger.info(
            "contents_reminder_scheduled",
            extra={
                "project_id": str(box.project_id),
                "box_id": str(box.id),
                "number": box.number.value,
                "notification_id": str(reminder.id),
                "remind_at": reminder.scheduled_at.isoformat(),
                "user_id": str(data.user_id),
            },
        )
        return view
