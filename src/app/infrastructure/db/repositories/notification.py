from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import ColumnElement, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.notification_repository import AbstractNotificationRepository
from app.domain.common.ids import BoxId, ProjectId, TaskId, UserId
from app.domain.common.reminders import MovingReminderOffset, ReminderOffset
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

    async def claim_next_due(self, now: datetime) -> Notification | None:
        # SKIP LOCKED: a row another worker is sending right now is passed over,
        # so parallel workers neither wait for each other nor send it twice.
        model = await self._session.scalar(
            select(_N)
            .where(_N.status == NotificationStatus.PENDING.value, _N.scheduled_at <= now)
            .order_by(_N.scheduled_at, _N.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        return None if model is None else NotificationMapper.to_domain(model)

    async def update(self, notification: Notification) -> None:
        await self._session.merge(NotificationMapper.to_model(notification))
        await self._session.flush()

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

    async def cancel_pending_for_task(
        self, task_id: TaskId, *, user_id: UserId | None = None
    ) -> int:
        conditions = [_N.task_id == task_id]
        if user_id is not None:
            conditions.append(_N.user_id == user_id)
        return await self._cancel(*conditions)

    async def cancel_pending_task_reminders(
        self, task_id: TaskId, *, offsets: Iterable[ReminderOffset] | None = None
    ) -> int:
        conditions = [_N.task_id == task_id, _N.type == NotificationType.TASK_REMINDER.value]
        if offsets is not None:
            conditions.append(_N.reminder_offset_hours.in_([offset.hours for offset in offsets]))
        return await self._cancel(*conditions)

    async def cancel_pending_for_box(self, box_id: BoxId) -> int:
        return await self._cancel(_N.box_id == box_id)

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
