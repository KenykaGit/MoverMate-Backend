import pytest

from app.application.notifications.dto import SubscribeDTO, UnsubscribeDTO
from app.application.notifications.get_vapid_public_key import GetVapidPublicKey
from app.application.notifications.subscribe_to_push import SubscribeToPush
from app.application.notifications.unsubscribe_from_push import UnsubscribeFromPush
from app.domain.common.ids import UserId, new_id
from app.domain.notifications.errors import InvalidPushSubscriptionError
from tests.fakes import FakePushKeys, FakeUnitOfWork, InMemoryPushSubscriptionRepository

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc"
ALICE, BOB = UserId(new_id()), UserId(new_id())


def subscribe_dto(user_id: UserId = ALICE, *, p256dh: str = "BNcRdreALRFX") -> SubscribeDTO:
    return SubscribeDTO(user_id=user_id, endpoint=ENDPOINT, p256dh=p256dh, auth="tBHItJI5svbpez7KI")


@pytest.fixture
def subscriptions() -> InMemoryPushSubscriptionRepository:
    return InMemoryPushSubscriptionRepository()


@pytest.fixture
def subscribe(
    subscriptions: InMemoryPushSubscriptionRepository, uow: FakeUnitOfWork
) -> SubscribeToPush:
    return SubscribeToPush(subscriptions, uow)


@pytest.fixture
def unsubscribe(
    subscriptions: InMemoryPushSubscriptionRepository, uow: FakeUnitOfWork
) -> UnsubscribeFromPush:
    return UnsubscribeFromPush(subscriptions, uow)


async def test_public_key() -> None:
    assert await GetVapidPublicKey(FakePushKeys())() == FakePushKeys.KEY


class TestSubscribe:
    async def test_new_browser(
        self, subscribe: SubscribeToPush, subscriptions: InMemoryPushSubscriptionRepository
    ) -> None:
        await subscribe(subscribe_dto())

        [stored] = await subscriptions.list_active_for_user(ALICE)
        assert stored.endpoint.value == ENDPOINT

    async def test_again_is_an_upsert_with_the_new_keys(
        self, subscribe: SubscribeToPush, subscriptions: InMemoryPushSubscriptionRepository
    ) -> None:
        await subscribe(subscribe_dto())
        await subscribe(subscribe_dto(p256dh="BNewKeyAfterRefresh"))

        [stored] = subscriptions.subscriptions.values()
        assert stored.keys.p256dh == "BNewKeyAfterRefresh"

    async def test_another_account_takes_the_browser_over(
        self, subscribe: SubscribeToPush, subscriptions: InMemoryPushSubscriptionRepository
    ) -> None:
        await subscribe(subscribe_dto(ALICE))
        await subscribe(subscribe_dto(BOB))

        assert await subscriptions.list_active_for_user(ALICE) == []
        assert len(await subscriptions.list_active_for_user(BOB)) == 1

    async def test_a_deactivated_browser_revives(
        self,
        subscribe: SubscribeToPush,
        unsubscribe: UnsubscribeFromPush,
        subscriptions: InMemoryPushSubscriptionRepository,
    ) -> None:
        await subscribe(subscribe_dto())
        await unsubscribe(UnsubscribeDTO(user_id=ALICE, endpoint=ENDPOINT))
        await subscribe(subscribe_dto())

        assert len(await subscriptions.list_active_for_user(ALICE)) == 1

    async def test_malformed(self, subscribe: SubscribeToPush, uow: FakeUnitOfWork) -> None:
        malformed = [
            SubscribeDTO(user_id=ALICE, endpoint="http://insecure.example/x", p256dh="A", auth="B"),
            SubscribeDTO(user_id=ALICE, endpoint=ENDPOINT, p256dh="not base64!", auth="B"),
        ]
        for dto in malformed:
            with pytest.raises(InvalidPushSubscriptionError):
                await subscribe(dto)
        assert uow.commits == 0


class TestUnsubscribe:
    async def test_own_browser(
        self,
        subscribe: SubscribeToPush,
        unsubscribe: UnsubscribeFromPush,
        subscriptions: InMemoryPushSubscriptionRepository,
    ) -> None:
        await subscribe(subscribe_dto())

        await unsubscribe(UnsubscribeDTO(user_id=ALICE, endpoint=ENDPOINT))
        await unsubscribe(UnsubscribeDTO(user_id=ALICE, endpoint=ENDPOINT))  # idempotent

        assert await subscriptions.list_active_for_user(ALICE) == []

    async def test_others_are_silently_ignored(
        self,
        subscribe: SubscribeToPush,
        unsubscribe: UnsubscribeFromPush,
        subscriptions: InMemoryPushSubscriptionRepository,
        uow: FakeUnitOfWork,
    ) -> None:
        await subscribe(subscribe_dto())

        await unsubscribe(UnsubscribeDTO(user_id=BOB, endpoint=ENDPOINT))  # someone else's
        await unsubscribe(UnsubscribeDTO(user_id=ALICE, endpoint="https://unknown.example/x"))
        await unsubscribe(UnsubscribeDTO(user_id=ALICE, endpoint="not a url"))

        assert len(await subscriptions.list_active_for_user(ALICE)) == 1
        assert uow.commits == 1  # the subscribe only
