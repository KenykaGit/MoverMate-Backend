from abc import ABC, abstractmethod

from app.domain.common.ids import UserId
from app.domain.notifications.push_subscription import PushEndpoint, PushSubscription


class AbstractPushSubscriptionRepository(ABC):
    """Web Push subscriptions (one per browser). Implementations must not commit."""

    @abstractmethod
    async def add(self, subscription: PushSubscription) -> PushSubscription:
        """Stage a new subscription and return what is stored.

        If a concurrent request stored the same endpoint meanwhile, that row is
        taken over (as PushSubscription.renew does) instead of failing: the
        endpoint is unique, and the browser behind it is the latest caller's.
        """

    @abstractmethod
    async def get_by_endpoint(self, endpoint: PushEndpoint) -> PushSubscription | None:
        """The subscription of this browser, active or not, locked until the
        transaction ends; None if it never subscribed."""

    @abstractmethod
    async def list_active_for_user(self, user_id: UserId) -> list[PushSubscription]:
        """Active subscriptions of the user, oldest first."""

    @abstractmethod
    async def update(self, subscription: PushSubscription) -> None:
        """Stage changes of an existing subscription (e.g. deactivation)."""
