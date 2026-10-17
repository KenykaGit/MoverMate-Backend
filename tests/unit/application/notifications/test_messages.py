from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.application.notifications.services import compose_message, days_word, moving_title
from app.domain.common.ids import BoxId, ProjectId, TaskId, UserId, new_id
from app.domain.common.reminders import MovingReminderOffset, ReminderOffset
from app.domain.notifications.notification import (
    BoxContentsReminder,
    MovingDateReminder,
    Notification,
    NotificationTarget,
    TaskAssigned,
    TaskReminder,
)
from tests.unit.application.notifications.test_deliver_next_notification import CONTEXT

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def notification(target: NotificationTarget) -> Notification:
    created = Notification.schedule(
        project_id=ProjectId(new_id()), user_id=UserId(new_id()), target=target, at=NOW, now=NOW
    )
    assert created is not None
    return created


def test_task_assigned() -> None:
    task_id = TaskId(new_id())
    target = notification(TaskAssigned(task_id))

    message = compose_message(target, CONTEXT)

    assert message is not None
    assert message.title == "Новая задача"
    assert message.body == "Вам назначили «Заказать грузчиков» в проекте «Переезд на Садовую»"
    assert (message.url, message.data["task_id"]) == ("/plan", str(task_id))
    assert message.data["type"] == "TASK_ASSIGNED"
    assert message.data["notification_id"] == str(target.id)


def test_task_reminder_shows_the_due_time_in_the_project_time_zone() -> None:
    message = compose_message(
        notification(TaskReminder(TaskId(new_id()), ReminderOffset(2))), CONTEXT
    )

    assert message is not None
    assert message.body == "«Заказать грузчиков» — срок 01.05 в 18:00"  # 15:00 UTC in Moscow


def test_box_contents_reminder_links_the_card_and_hides_the_contents() -> None:
    box_id = BoxId(new_id())

    message = compose_message(notification(BoxContentsReminder(box_id)), CONTEXT)

    assert message is not None
    assert message.title == "Опишите коробку № 12"
    assert message.url == f"/b/{'q' * 22}"
    assert message.data["box_id"] == str(box_id)


@pytest.mark.parametrize(
    ("days", "title"),
    [
        (0, "Сегодня переезд"),
        (1, "Завтра переезд"),
        (2, "До переезда 2 дня"),
        (5, "До переезда 5 дней"),
        (11, "До переезда 11 дней"),
        (21, "До переезда 21 день"),
        (112, "До переезда 112 дней"),
        (365, "До переезда 365 дней"),
    ],
)
def test_moving_date_titles(days: int, title: str) -> None:
    message = compose_message(notification(MovingDateReminder(MovingReminderOffset(days))), CONTEXT)

    assert message is not None
    assert message.title == moving_title(days) == title
    assert message.url == "/dashboard"


@pytest.mark.parametrize(
    ("days", "word"),
    [(1, "день"), (3, "дня"), (12, "дней"), (14, "дней"), (22, "дня"), (100, "дней")],
)
def test_days_word(days: int, word: str) -> None:
    assert days_word(days) == word


def test_long_names_are_shortened() -> None:
    context = replace(CONTEXT, project_name="П" * 300)

    message = compose_message(notification(MovingDateReminder(MovingReminderOffset(3))), context)

    assert message is not None
    assert len(message.body) < 150
    assert "…" in message.body
