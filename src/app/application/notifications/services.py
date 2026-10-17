"""Texts of notifications, built at sending time from current data.

Returns None when the reason of a notification is gone (docs/db-design.md, 10.1):
then it is cancelled instead of sent. Box contents never appear in a text: a
lock screen is visible to anyone nearby.
"""

from app.application.interfaces.notification_context import NotificationContext
from app.application.interfaces.push_sender import PushMessage
from app.domain.notifications.notification import (
    BoxContentsReminder,
    MovingDateReminder,
    Notification,
    TaskAssigned,
    TaskReminder,
)
from app.domain.projects.value_objects import TimeZone

PACKING = "PACKING"
TITLE_MAX_LENGTH = 80


def compose_message(notification: Notification, context: NotificationContext) -> PushMessage | None:
    """The message to send, or None if there is no longer a reason to send it."""
    reachable = (
        context.project_is_active and context.recipient_is_active and context.recipient_is_member
    )
    if not reachable:
        return None
    data = {
        "type": notification.type.value,
        "project_id": str(notification.project_id),
        "notification_id": str(notification.id),
    }
    project = _short(context.project_name)
    match notification.target:
        case TaskAssigned():
            return _task_assigned(context, project, data | {"task_id": _task_id(notification)})
        case TaskReminder():
            return _task_reminder(context, data | {"task_id": _task_id(notification)})
        case BoxContentsReminder(box_id=box_id):
            return _box_contents(context, project, data | {"box_id": str(box_id)})
        case MovingDateReminder(offset=offset):
            return PushMessage(
                title=moving_title(offset.days),
                body=f"Проект «{project}»: проверьте план и коробки",
                url="/dashboard",
                data=data,
            )


def _task_assigned(
    context: NotificationContext, project: str, data: dict[str, str]
) -> PushMessage | None:
    task = context.task
    if task is None or not task.is_active:
        return None
    return PushMessage(
        title="Новая задача",
        body=f"Вам назначили «{_short(task.title)}» в проекте «{project}»",
        url="/plan",
        data=data,
    )


def _task_reminder(context: NotificationContext, data: dict[str, str]) -> PushMessage | None:
    task = context.task
    if task is None or not task.is_active or task.is_done or task.due_at is None:
        return None
    # The project's zone: where the move, and so the task, takes place.
    due = task.due_at.astimezone(TimeZone(context.project_time_zone).zone)
    return PushMessage(
        title="Скоро срок задачи",
        body=f"«{_short(task.title)}» — срок {due:%d.%m в %H:%M}",
        url="/plan",
        data=data,
    )


def _box_contents(
    context: NotificationContext, project: str, data: dict[str, str]
) -> PushMessage | None:
    box = context.box
    if box is None or not box.is_active or box.status != PACKING:
        return None
    return PushMessage(
        title=f"Опишите коробку № {box.number}",
        body=f"Проект «{project}»: добавьте вещи, пока коробка не собрана",
        url=f"/b/{box.qr_token}",
        data=data,
    )


def moving_title(days: int) -> str:
    special = {0: "Сегодня переезд", 1: "Завтра переезд"}
    return special.get(days) or f"До переезда {days} {days_word(days)}"


def days_word(days: int) -> str:
    """Russian plural: 1 день, 2 дня, 5 дней, 11 дней, 21 день."""
    last, last_two = days % 10, days % 100
    if last_two in _TEENS:
        return "дней"
    return _BY_LAST_DIGIT.get(last, "дней")


_TEENS = range(11, 15)
_BY_LAST_DIGIT = {1: "день", 2: "дня", 3: "дня", 4: "дня"}


def _task_id(notification: Notification) -> str:
    match notification.target:
        case TaskAssigned(task_id=task_id) | TaskReminder(task_id=task_id):
            return str(task_id)
    raise TypeError(notification.target)  # pragma: no cover


def _short(text: str) -> str:
    return text if len(text) <= TITLE_MAX_LENGTH else text[: TITLE_MAX_LENGTH - 1] + "…"
