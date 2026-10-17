from datetime import timedelta

import pytest

from app.domain.common.ids import BoxId, ProjectId, TaskId, UserId, new_id
from app.domain.common.reminders import MovingReminderOffset, ReminderOffset
from app.domain.notifications.errors import (
    InvalidPushSubscriptionError,
    NotificationAlreadyFinishedError,
)
from app.domain.notifications.notification import (
    MAX_ATTEMPTS,
    NOTIFICATION_TRANSITIONS,
    RETRY_BASE_DELAY,
    BoxContentsReminder,
    MovingDateReminder,
    Notification,
    NotificationStatus,
    NotificationTarget,
    NotificationType,
    TaskAssigned,
    TaskReminder,
)
from app.domain.notifications.push_subscription import PushEndpoint, PushKeys, PushSubscription
from tests.checks import Rejection, not_rejected
from tests.factories import NOW

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc"
KEYS = PushKeys(
    p256dh="BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u",
    auth="tBHItJI5svbpez7KI4CCXg",
)


def schedule(
    target: NotificationTarget | None = None, at_offset: timedelta = timedelta()
) -> Notification:
    notification = Notification.schedule(
        project_id=ProjectId(new_id()),
        user_id=UserId(new_id()),
        target=target or TaskAssigned(TaskId(new_id())),
        at=NOW + at_offset,
        now=NOW,
    )
    assert notification is not None
    return notification


class TestSchedule:
    def test_is_pending(self) -> None:
        notification = schedule(at_offset=timedelta(hours=1))

        assert notification.is_pending
        assert notification.scheduled_at == NOW + timedelta(hours=1)
        assert notification.attempts == 0

    def test_a_moment_in_the_past_is_not_scheduled(self) -> None:
        assert (
            Notification.schedule(
                project_id=ProjectId(new_id()),
                user_id=UserId(new_id()),
                target=BoxContentsReminder(BoxId(new_id())),
                at=NOW - timedelta(seconds=1),
                now=NOW,
            )
            is None
        )

    def test_type_follows_the_target(self) -> None:
        cases: list[tuple[NotificationTarget, NotificationType]] = [
            (TaskAssigned(TaskId(new_id())), NotificationType.TASK_ASSIGNED),
            (TaskReminder(TaskId(new_id()), ReminderOffset(2)), NotificationType.TASK_REMINDER),
            (BoxContentsReminder(BoxId(new_id())), NotificationType.BOX_CONTENTS_REMINDER),
            (MovingDateReminder(MovingReminderOffset(7)), NotificationType.MOVING_DATE_REMINDER),
        ]

        assert [schedule(target).type for target, _ in cases] == [expected for _, expected in cases]


class TestDelivery:
    def test_transitions_leave_pending_only(self) -> None:
        assert {source for source, _ in NOTIFICATION_TRANSITIONS} == {NotificationStatus.PENDING}
        assert {target for _, target in NOTIFICATION_TRANSITIONS} == set(NotificationStatus) - {
            NotificationStatus.PENDING
        }

    def test_sent_skipped_and_cancelled(self) -> None:
        sent, skipped, cancelled = schedule(), schedule(), schedule()

        sent.mark_sent(now=NOW)
        skipped.mark_skipped()
        assert cancelled.cancel()
        assert not cancelled.cancel()

        assert (sent.status, sent.sent_at) == (NotificationStatus.SENT, NOW)
        assert skipped.status is NotificationStatus.SKIPPED
        assert cancelled.status is NotificationStatus.CANCELLED

    def test_failures_back_off_exponentially_then_give_up(self) -> None:
        notification = schedule()

        for attempt in range(1, MAX_ATTEMPTS):
            notification.record_failure("timeout", now=NOW)
            assert notification.is_pending
            assert notification.scheduled_at == NOW + RETRY_BASE_DELAY * 2 ** (attempt - 1)

        notification.record_failure("x" * 5000, now=NOW)

        assert notification.status is NotificationStatus.FAILED
        assert notification.attempts == MAX_ATTEMPTS
        assert notification.last_error is not None
        assert len(notification.last_error) == 1000

    def test_finished_notifications_cannot_be_sent_again(self) -> None:
        notification = schedule()
        notification.mark_sent(now=NOW)

        with pytest.raises(NotificationAlreadyFinishedError):
            notification.mark_sent(now=NOW)
        with pytest.raises(NotificationAlreadyFinishedError):
            notification.record_failure("late", now=NOW)
        assert not notification.cancel()
        assert notification.status is NotificationStatus.SENT


class TestPushSubscription:
    def test_endpoint_must_be_https_and_keys_base64url(self) -> None:
        endpoints = ["http://push.example/1", "push.example", "https://" + "a" * 2048]
        keys = [("", "abc"), ("abc", "not base64!")]
        cases: list[Rejection] = [
            *((PushEndpoint, value, InvalidPushSubscriptionError) for value in endpoints),
            *((lambda pair: PushKeys(*pair), pair, InvalidPushSubscriptionError) for pair in keys),
        ]

        assert not_rejected(cases) == []
        assert KEYS.auth not in repr(KEYS), "the keys are secrets"

    def test_renew_moves_it_to_another_user_and_reactivates(self) -> None:
        subscription = PushSubscription.subscribe(
            user_id=UserId(new_id()), endpoint=PushEndpoint(ENDPOINT), keys=KEYS
        )
        subscription.deactivate()
        other = UserId(new_id())

        subscription.renew(user_id=other, keys=KEYS)

        assert subscription.user_id == other
        assert subscription.is_active
