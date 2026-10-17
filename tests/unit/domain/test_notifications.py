from datetime import timedelta

import pytest

from app.domain.common.ids import BoxId, ProjectId, TaskId, UserId, new_id
from app.domain.common.reminders import ReminderOffset
from app.domain.notifications.errors import (
    InvalidPushSubscriptionError,
    NotificationAlreadyFinishedError,
)
from app.domain.notifications.notification import (
    MAX_ATTEMPTS,
    NOTIFICATION_TRANSITIONS,
    RETRY_BASE_DELAY,
    BoxContentsReminder,
    Notification,
    NotificationStatus,
    NotificationTarget,
    NotificationType,
    TaskAssigned,
    TaskReminder,
)
from app.domain.notifications.push_subscription import PushEndpoint, PushKeys, PushSubscription
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

    @pytest.mark.parametrize(
        ("target", "expected"),
        [
            (TaskAssigned(TaskId(new_id())), NotificationType.TASK_ASSIGNED),
            (TaskReminder(TaskId(new_id()), ReminderOffset(2)), NotificationType.TASK_REMINDER),
            (BoxContentsReminder(BoxId(new_id())), NotificationType.BOX_CONTENTS_REMINDER),
        ],
    )
    def test_type_follows_the_target(
        self, target: NotificationTarget, expected: NotificationType
    ) -> None:
        assert schedule(target).type is expected


class TestDelivery:
    def test_transitions_leave_pending_only(self) -> None:
        assert {source for source, _ in NOTIFICATION_TRANSITIONS} == {NotificationStatus.PENDING}
        assert {target for _, target in NOTIFICATION_TRANSITIONS} == set(NotificationStatus) - {
            NotificationStatus.PENDING
        }

    def test_mark_sent(self) -> None:
        notification = schedule()

        notification.mark_sent(now=NOW)

        assert notification.status is NotificationStatus.SENT
        assert notification.sent_at == NOW

    def test_mark_skipped(self) -> None:
        notification = schedule()

        notification.mark_skipped()

        assert notification.status is NotificationStatus.SKIPPED

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

    def test_cancel_only_while_pending(self) -> None:
        notification = schedule()

        assert notification.cancel()
        assert not notification.cancel()
        assert notification.status is NotificationStatus.CANCELLED

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
    @pytest.mark.parametrize(
        "value", ["http://push.example/1", "push.example", "https://" + "a" * 2048]
    )
    def test_endpoint_must_be_https(self, value: str) -> None:
        with pytest.raises(InvalidPushSubscriptionError):
            PushEndpoint(value)

    @pytest.mark.parametrize(("p256dh", "auth"), [("", "abc"), ("abc", "not base64!")])
    def test_keys_must_be_base64url(self, p256dh: str, auth: str) -> None:
        with pytest.raises(InvalidPushSubscriptionError):
            PushKeys(p256dh=p256dh, auth=auth)

    def test_keys_are_hidden_from_repr(self) -> None:
        assert KEYS.auth not in repr(KEYS)

    def test_renew_moves_it_to_another_user_and_reactivates(self) -> None:
        subscription = PushSubscription.subscribe(
            user_id=UserId(new_id()), endpoint=PushEndpoint(ENDPOINT), keys=KEYS
        )
        subscription.deactivate()
        other = UserId(new_id())

        subscription.renew(user_id=other, keys=KEYS)

        assert subscription.user_id == other
        assert subscription.is_active
