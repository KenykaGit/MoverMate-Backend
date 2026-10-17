from collections.abc import Iterable

from sqlalchemy import ColumnElement, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.notification_repository import AbstractNotificationRepository
from app.domain.common.ids import ProjectId, UserId
from app.domain.common.reminders import MovingReminderOffset
from app.domain.notifications.notification import (
    Notification,
    NotificationStatus,
    NotificationType,
)
from app.infrastructure.db.mappers.notification import NotificationMapper
from app.infrastructure.db.models.notification import NotificationModel

_N = NotificationModel


class SqlAlchemyNotificationRepository(AbstractNotificationRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, notification: Notification) -> None:
        self._session.add(NotificationMapper.to_model(notification))

    async def cancel_pending(self, project_id: ProjectId, *, user_id: UserId | None = None) -> int:
        conditions = [_N.project_id == project_id]
        if user_id is not None:
            conditions.append(_N.user_id == user_id)
        return await self._cancel(*conditions)

    async def cancel_pending_moving_reminders(
        self, project_id: ProjectId, *, offsets: Iterable[MovingReminderOffset] | None = None
    ) -> int:
        conditions = [
            _N.project_id == project_id,
            _N.type == NotificationType.MOVING_DATE_REMINDER.value,
        ]
        if offsets is not None:
            conditions.append(_N.reminder_offset_days.in_([offset.days for offset in offsets]))
        return await self._cancel(*conditions)

    async def _cancel(self, *conditions: ColumnElement[bool]) -> int:
        # The domain rule "only a pending notification changes" is the WHERE clause;
        # the status trigger of the database guards it as well.
        result = await self._session.execute(
            update(_N)
            .where(_N.status == NotificationStatus.PENDING.value, *conditions)
            .values(status=NotificationStatus.CANCELLED.value)
            .returning(_N.id)
        )
        return len(result.all())
