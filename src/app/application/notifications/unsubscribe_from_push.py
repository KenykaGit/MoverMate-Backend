import logging

from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.push_subscription_repository import (
    AbstractPushSubscriptionRepository,
)
from app.application.notifications.dto import UnsubscribeDTO
from app.domain.notifications.errors import InvalidPushSubscriptionError
from app.domain.notifications.push_subscription import PushEndpoint

logger = logging.getLogger(__name__)


class UnsubscribeFromPush:
    def __init__(
        self, subscriptions: AbstractPushSubscriptionRepository, uow: AbstractUnitOfWork
    ) -> None:
        self._subscriptions = subscriptions
        self._uow = uow

    async def __call__(self, data: UnsubscribeDTO) -> None:
        """Stop pushing to this browser. Idempotent, and silent about endpoints
        that are unknown or belong to someone else: the answer must not reveal
        whose browser an endpoint is."""
        try:
            endpoint = PushEndpoint(data.endpoint)
        except InvalidPushSubscriptionError:
            return  # could never have been stored
        subscription = await self._subscriptions.get_by_endpoint(endpoint)
        if subscription is None or subscription.user_id != data.user_id:
            return
        if not subscription.is_active:
            return

        subscription.deactivate()
        await self._subscriptions.update(subscription)
        await self._uow.commit()
        logger.info(
            "push_unsubscribed",
            extra={"user_id": str(data.user_id), "subscription_id": str(subscription.id)},
        )
