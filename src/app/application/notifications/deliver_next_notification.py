import logging
from datetime import datetime

from app.application.common.clock import utc_now
from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.notification_context import AbstractNotificationContextReader
from app.application.interfaces.notification_repository import AbstractNotificationRepository
from app.application.interfaces.push_sender import (
    AbstractPushSender,
    PushMessage,
    PushOutcome,
)
from app.application.interfaces.push_subscription_repository import (
    AbstractPushSubscriptionRepository,
)
from app.application.notifications.dto import DeliveryOutcome
from app.application.notifications.services import compose_message
from app.domain.notifications.notification import Notification, NotificationStatus

logger = logging.getLogger(__name__)


class DeliverNextNotification:
    """One step of the worker: one due notification, in its own transaction."""

    def __init__(
        self,
        notifications: AbstractNotificationRepository,
        contexts: AbstractNotificationContextReader,
        subscriptions: AbstractPushSubscriptionRepository,
        sender: AbstractPushSender,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._notifications = notifications
        self._contexts = contexts
        self._subscriptions = subscriptions
        self._sender = sender
        self._uow = uow

    async def __call__(self) -> DeliveryOutcome | None:
        """Send the notification due longest ago; None if nothing is due.

        The reason is checked again first (the task may be done by now). The
        message goes to every active browser of the recipient; a browser the push
        service reports gone is deactivated. Delivered to at least one: SENT.
        Nowhere to deliver: SKIPPED. Only failures: retried with a growing delay,
        FAILED after the last attempt. The row stays locked while sending, so no
        other worker sends it twice.
        """
        now = utc_now()
        notification = await self._notifications.claim_next_due(now)
        if notification is None:
            return None
        delay = now - notification.scheduled_at

        message = compose_message(notification, await self._contexts.get(notification))
        if message is None:
            notification.cancel()
            outcome = DeliveryOutcome.CANCELLED
        else:
            outcome = await self._send(notification, message, now=now)
        await self._notifications.update(notification)
        await self._uow.commit()

        log = logger.warning if outcome is DeliveryOutcome.FAILED else logger.info
        log(
            "notification_processed",
            extra={
                "notification_id": str(notification.id),
                "notification_type": notification.type.value,
                "project_id": str(notification.project_id),
                "user_id": str(notification.user_id),
                "outcome": outcome.value,
                "attempts": notification.attempts,
                "delay_seconds": round(delay.total_seconds()),
            },
        )
        return outcome

    async def _send(
        self, notification: Notification, message: PushMessage, *, now: datetime
    ) -> DeliveryOutcome:
        subscriptions = await self._subscriptions.list_active_for_user(notification.user_id)
        errors: list[str] = []
        delivered = False
        for subscription in subscriptions:
            result = await self._sender.send(subscription, message)
            match result.outcome:
                case PushOutcome.DELIVERED:
                    delivered = True
                case PushOutcome.GONE:
                    subscription.deactivate()
                    await self._subscriptions.update(subscription)
                    logger.info(
                        "push_subscription_gone",
                        extra={
                            "subscription_id": str(subscription.id),
                            "user_id": str(subscription.user_id),
                            "reason": result.detail,
                        },
                    )
                case PushOutcome.FAILED:
                    errors.append(result.detail or "push service error")

        if delivered:
            notification.mark_sent(now=now)
            return DeliveryOutcome.SENT
        if not errors:  # no subscriptions, or all of them are gone
            notification.mark_skipped()
            return DeliveryOutcome.SKIPPED
        notification.record_failure("; ".join(errors), now=now)
        if notification.status is NotificationStatus.FAILED:
            return DeliveryOutcome.FAILED
        return DeliveryOutcome.RETRY
