from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.application.interfaces.notification_context import (
    BoxFacts,
    NotificationContext,
    TaskFacts,
)
from app.application.interfaces.push_sender import PushOutcome, PushResult
from app.application.notifications.deliver_next_notification import DeliverNextNotification
from app.application.notifications.dto import DeliveryOutcome
from app.domain.common.ids import BoxId, ProjectId, TaskId, UserId, new_id
from app.domain.common.reminders import MovingReminderOffset, ReminderOffset
from app.domain.notifications.notification import (
    MAX_ATTEMPTS,
    BoxContentsReminder,
    MovingDateReminder,
    Notification,
    NotificationStatus,
    NotificationTarget,
    TaskAssigned,
    TaskReminder,
)
from app.domain.notifications.push_subscription import PushEndpoint, PushKeys, PushSubscription
from tests.fakes import (
    FakeNotificationContextReader,
    FakePushSender,
    FakeUnitOfWork,
    InMemoryNotificationRepository,
    InMemoryPushSubscriptionRepository,
)

USER = UserId(new_id())
TASK = TaskFacts(
    title="Заказать грузчиков",
    due_at=datetime(2030, 5, 1, 15, tzinfo=UTC),
    is_done=False,
    is_active=True,
)
BOX = BoxFacts(number=12, status="PACKING", qr_token="q" * 22, is_active=True)
CONTEXT = NotificationContext(
    project_name="Переезд на Садовую",
    project_time_zone="Europe/Moscow",
    project_is_active=True,
    recipient_is_active=True,
    recipient_is_member=True,
    task=TASK,
    box=BOX,
)


def due(
    target: NotificationTarget | None = None, *, ago: timedelta = timedelta(minutes=1)
) -> Notification:
    """A pending notification to USER that was due `ago`."""
    past = datetime.now(UTC) - ago
    notification = Notification.schedule(
        project_id=ProjectId(new_id()),
        user_id=USER,
        target=target or MovingDateReminder(MovingReminderOffset(1)),
        at=past,
        now=past,
    )
    assert notification is not None
    return notification


def browser(name: str = "one", *, user_id: UserId = USER) -> PushSubscription:
    return PushSubscription.subscribe(
        user_id=user_id,
        endpoint=PushEndpoint(f"https://push.example/{name}"),
        keys=PushKeys(p256dh="BNcRdreALRFX", auth="tBHItJI5svbpez7KI4CCXg"),
    )


@pytest.fixture
def subscriptions() -> InMemoryPushSubscriptionRepository:
    return InMemoryPushSubscriptionRepository([browser()])


@pytest.fixture
def sender() -> FakePushSender:
    return FakePushSender()


@pytest.fixture
def contexts() -> FakeNotificationContextReader:
    return FakeNotificationContextReader(CONTEXT)


@pytest.fixture
def deliver(
    notifications: InMemoryNotificationRepository,
    contexts: FakeNotificationContextReader,
    subscriptions: InMemoryPushSubscriptionRepository,
    sender: FakePushSender,
    uow: FakeUnitOfWork,
) -> DeliverNextNotification:
    return DeliverNextNotification(notifications, contexts, subscriptions, sender, uow)


async def test_nothing_due(
    deliver: DeliverNextNotification,
    notifications: InMemoryNotificationRepository,
    uow: FakeUnitOfWork,
) -> None:
    future = Notification.schedule(
        project_id=ProjectId(new_id()),
        user_id=USER,
        target=MovingDateReminder(MovingReminderOffset(1)),
        at=datetime.now(UTC) + timedelta(hours=1),
        now=datetime.now(UTC),
    )
    assert future is not None
    await notifications.add(future)

    assert await deliver() is None
    assert uow.commits == 0


async def test_sends_the_oldest_due_one_to_every_browser(
    deliver: DeliverNextNotification,
    notifications: InMemoryNotificationRepository,
    subscriptions: InMemoryPushSubscriptionRepository,
    sender: FakePushSender,
    uow: FakeUnitOfWork,
) -> None:
    await subscriptions.update(browser("two"))
    await subscriptions.update(browser("stranger", user_id=UserId(new_id())))
    newer, older = due(ago=timedelta(minutes=1)), due(ago=timedelta(minutes=5))
    for notification in (newer, older):
        await notifications.add(notification)

    assert await deliver() is DeliveryOutcome.SENT

    stored = notifications.get(older)
    assert stored.status is NotificationStatus.SENT
    assert stored.sent_at is not None
    assert notifications.get(newer).is_pending
    assert sorted(s.endpoint.value for s, _ in sender.sent) == [
        "https://push.example/one",
        "https://push.example/two",
    ]
    assert uow.commits == 1


async def test_without_subscriptions_it_is_skipped(
    deliver: DeliverNextNotification,
    notifications: InMemoryNotificationRepository,
    subscriptions: InMemoryPushSubscriptionRepository,
    sender: FakePushSender,
) -> None:
    subscriptions.subscriptions.clear()
    notification = due()
    await notifications.add(notification)

    assert await deliver() is DeliveryOutcome.SKIPPED
    assert notifications.get(notification).status is NotificationStatus.SKIPPED
    assert sender.sent == []


async def test_gone_browsers_are_deactivated(
    deliver: DeliverNextNotification,
    notifications: InMemoryNotificationRepository,
    subscriptions: InMemoryPushSubscriptionRepository,
    sender: FakePushSender,
) -> None:
    alive = browser("alive")
    await subscriptions.update(alive)
    sender.results["https://push.example/one"] = PushResult(PushOutcome.GONE, "410")
    notification = due()
    await notifications.add(notification)

    assert await deliver() is DeliveryOutcome.SENT  # the other browser got it
    assert [s.endpoint.value for s in await subscriptions.list_active_for_user(USER)] == [
        alive.endpoint.value
    ]


async def test_all_browsers_gone_means_skipped(
    deliver: DeliverNextNotification,
    notifications: InMemoryNotificationRepository,
    subscriptions: InMemoryPushSubscriptionRepository,
    sender: FakePushSender,
) -> None:
    sender.results["https://push.example/one"] = PushResult(PushOutcome.GONE, "404")
    notification = due()
    await notifications.add(notification)

    assert await deliver() is DeliveryOutcome.SKIPPED
    assert await subscriptions.list_active_for_user(USER) == []


async def test_failures_are_retried_with_a_delay_then_given_up(
    deliver: DeliverNextNotification,
    notifications: InMemoryNotificationRepository,
    sender: FakePushSender,
) -> None:
    sender.results["https://push.example/one"] = PushResult(PushOutcome.FAILED, "503")
    notification = due()
    await notifications.add(notification)

    assert await deliver() is DeliveryOutcome.RETRY
    stored = notifications.get(notification)
    assert (stored.status, stored.attempts, stored.last_error) == (
        NotificationStatus.PENDING,
        1,
        "503",
    )
    assert stored.scheduled_at > datetime.now(UTC)  # not due again right away
    assert await deliver() is None

    # Fast-forward: make it due again until the last allowed attempt.
    for attempt in range(2, MAX_ATTEMPTS + 1):
        stored = notifications.get(notification)
        await notifications.update(replace(stored, scheduled_at=datetime.now(UTC)))
        outcome = await deliver()
        expected = DeliveryOutcome.FAILED if attempt == MAX_ATTEMPTS else DeliveryOutcome.RETRY
        assert outcome is expected
    assert notifications.get(notification).status is NotificationStatus.FAILED


async def test_cancelled_when_the_reason_is_gone(
    deliver: DeliverNextNotification,
    notifications: InMemoryNotificationRepository,
    contexts: FakeNotificationContextReader,
    sender: FakePushSender,
) -> None:
    cases: list[tuple[NotificationTarget, dict[str, object]]] = [
        (MovingDateReminder(MovingReminderOffset(1)), {"project_is_active": False}),
        (MovingDateReminder(MovingReminderOffset(1)), {"recipient_is_active": False}),
        (MovingDateReminder(MovingReminderOffset(1)), {"recipient_is_member": False}),
        (TaskAssigned(TaskId(new_id())), {"task": replace(TASK, is_active=False)}),
        (
            TaskReminder(TaskId(new_id()), ReminderOffset(2)),
            {"task": replace(TASK, is_done=True)},
        ),
        (
            TaskReminder(TaskId(new_id()), ReminderOffset(2)),
            {"task": replace(TASK, due_at=None)},
        ),
        (BoxContentsReminder(BoxId(new_id())), {"box": replace(BOX, status="PACKED")}),
        (BoxContentsReminder(BoxId(new_id())), {"box": replace(BOX, is_active=False)}),
    ]
    wrong = []
    for target, facts in cases:
        contexts.context = replace(CONTEXT, **facts)  # type: ignore[arg-type]
        notification = due(target)
        await notifications.add(notification)
        outcome = await deliver()
        status = notifications.get(notification).status
        if (outcome, status) != (DeliveryOutcome.CANCELLED, NotificationStatus.CANCELLED):
            wrong.append((target, facts, outcome, status))

    assert wrong == []
    assert sender.sent == []
