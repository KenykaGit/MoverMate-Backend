from app.domain.common.ids import BoxId, ProjectId, TaskId, UserId
from app.domain.common.reminders import MovingReminderOffset, ReminderOffset
from app.domain.notifications.notification import (
    BoxContentsReminder,
    MovingDateReminder,
    Notification,
    NotificationId,
    NotificationStatus,
    NotificationTarget,
    NotificationType,
    TaskAssigned,
    TaskReminder,
)
from app.domain.notifications.push_subscription import (
    PushEndpoint,
    PushKeys,
    PushSubscription,
    PushSubscriptionId,
)
from app.infrastructure.db.models.notification import NotificationModel
from app.infrastructure.db.models.push_subscription import PushSubscriptionModel


class InvalidNotificationRowError(Exception):
    """A row whose columns do not fit its type: the database CHECK should make it impossible."""


class NotificationMapper:
    """The domain target (a sum type) is spread over type, task_id, box_id and the offsets."""

    @staticmethod
    def to_domain(model: NotificationModel) -> Notification:
        return Notification(
            id=NotificationId(model.id),
            project_id=ProjectId(model.project_id),
            user_id=UserId(model.user_id),
            target=_target(model),
            scheduled_at=model.scheduled_at,
            status=NotificationStatus(model.status),
            attempts=model.attempts,
            last_error=model.last_error,
            sent_at=model.sent_at,
        )

    @staticmethod
    def to_model(notification: Notification) -> NotificationModel:
        task_id: TaskId | None = None
        box_id: BoxId | None = None
        offset_hours: int | None = None
        offset_days: int | None = None
        match notification.target:
            case TaskAssigned(task_id=assigned):
                task_id = assigned
            case TaskReminder(task_id=reminded, offset=offset):
                task_id, offset_hours = reminded, offset.hours
            case BoxContentsReminder(box_id=box):
                box_id = box
            case MovingDateReminder(offset=moving_offset):
                offset_days = moving_offset.days
        return NotificationModel(
            id=notification.id,
            project_id=notification.project_id,
            user_id=notification.user_id,
            type=notification.type.value,
            task_id=task_id,
            box_id=box_id,
            reminder_offset_hours=offset_hours,
            reminder_offset_days=offset_days,
            scheduled_at=notification.scheduled_at,
            status=notification.status.value,
            attempts=notification.attempts,
            last_error=notification.last_error,
            sent_at=notification.sent_at,
        )


def _target(model: NotificationModel) -> NotificationTarget:
    columns = (
        NotificationType(model.type),
        model.task_id,
        model.box_id,
        model.reminder_offset_hours,
        model.reminder_offset_days,
    )
    match columns:
        case NotificationType.TASK_ASSIGNED, task_id, None, None, None if task_id is not None:
            return TaskAssigned(TaskId(task_id))
        case NotificationType.TASK_REMINDER, task_id, None, int(hours), None if task_id is not None:
            return TaskReminder(TaskId(task_id), ReminderOffset(hours))
        case NotificationType.BOX_CONTENTS_REMINDER, None, box_id, None, None if box_id is not None:
            return BoxContentsReminder(BoxId(box_id))
        case NotificationType.MOVING_DATE_REMINDER, None, None, None, int(days):
            return MovingDateReminder(MovingReminderOffset(days))
    raise InvalidNotificationRowError(
        f"Notification {model.id} has columns that do not fit {model.type}"
    )


class PushSubscriptionMapper:
    @staticmethod
    def to_domain(model: PushSubscriptionModel) -> PushSubscription:
        return PushSubscription(
            id=PushSubscriptionId(model.id),
            user_id=UserId(model.user_id),
            endpoint=PushEndpoint(model.endpoint),
            keys=PushKeys(p256dh=model.p256dh, auth=model.auth),
            is_active=model.is_active,
        )

    @staticmethod
    def to_model(subscription: PushSubscription) -> PushSubscriptionModel:
        return PushSubscriptionModel(
            id=subscription.id,
            user_id=subscription.user_id,
            endpoint=subscription.endpoint.value,
            p256dh=subscription.keys.p256dh,
            auth=subscription.keys.auth,
            is_active=subscription.is_active,
        )
