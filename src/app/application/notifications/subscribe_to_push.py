import logging
from urllib.parse import urlsplit

from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.push_subscription_repository import (
    AbstractPushSubscriptionRepository,
)
from app.application.notifications.dto import SubscribeDTO
from app.domain.common.ids import UserId
from app.domain.notifications.push_subscription import PushEndpoint, PushKeys, PushSubscription

logger = logging.getLogger(__name__)


class SubscribeToPush:
    def __init__(
        self, subscriptions: AbstractPushSubscriptionRepository, uow: AbstractUnitOfWork
    ) -> None:
        self._subscriptions = subscriptions
        self._uow = uow

    async def __call__(self, data: SubscribeDTO) -> None:
        """Upsert by endpoint: this browser now gets the user's notifications.

        Idempotent. A browser that was subscribed under another account (shared
        device, re-login) is taken over by the caller; a deactivated one revives
        with its new keys.

        Raises:
            InvalidPushSubscriptionError: the endpoint is not https or a key is malformed.
        """
        user_id = UserId(data.user_id)
        endpoint = PushEndpoint(data.endpoint)
        keys = PushKeys(p256dh=data.p256dh, auth=data.auth)

        existing = await self._subscriptions.get_by_endpoint(endpoint)
        if existing is None:
            stored = await self._subscriptions.add(
                PushSubscription.subscribe(user_id=user_id, endpoint=endpoint, keys=keys)
            )
        else:
            existing.renew(user_id=user_id, keys=keys)
            await self._subscriptions.update(existing)
            stored = existing
        await self._uow.commit()

        logger.info(
            "push_subscribed",
            extra={
                "user_id": str(user_id),
                "subscription_id": str(stored.id),
                "renewed": existing is not None,
                "push_service": urlsplit(endpoint.value).netloc,
            },
        )
