from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.push_subscription_repository import (
    AbstractPushSubscriptionRepository,
)
from app.domain.common.ids import UserId
from app.domain.notifications.push_subscription import PushEndpoint, PushSubscription
from app.infrastructure.db.mappers.notification import PushSubscriptionMapper
from app.infrastructure.db.models.push_subscription import PushSubscriptionModel

_S = PushSubscriptionModel


class SqlAlchemyPushSubscriptionRepository(AbstractPushSubscriptionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, subscription: PushSubscription) -> PushSubscription:
        model = PushSubscriptionMapper.to_model(subscription)
        values = {
            "id": model.id,
            "user_id": model.user_id,
            "endpoint": model.endpoint,
            "p256dh": model.p256dh,
            "auth": model.auth,
            "is_active": model.is_active,
        }
        new = insert(_S).values(values)
        # A concurrent subscribe of the same browser: take its row over (renew).
        upsert = new.on_conflict_do_update(
            index_elements=[_S.endpoint],
            set_={
                "user_id": new.excluded.user_id,
                "p256dh": new.excluded.p256dh,
                "auth": new.excluded.auth,
                "is_active": True,
            },
        ).returning(_S)
        result = await self._session.execute(upsert, execution_options={"populate_existing": True})
        return PushSubscriptionMapper.to_domain(result.scalar_one())

    async def get_by_endpoint(self, endpoint: PushEndpoint) -> PushSubscription | None:
        model = await self._session.scalar(
            select(_S).where(_S.endpoint == endpoint.value).with_for_update()
        )
        return None if model is None else PushSubscriptionMapper.to_domain(model)

    async def list_active_for_user(self, user_id: UserId) -> list[PushSubscription]:
        models = await self._session.scalars(
            select(_S).where(_S.user_id == user_id, _S.is_active).order_by(_S.created_at, _S.id)
        )
        return [PushSubscriptionMapper.to_domain(model) for model in models]

    async def update(self, subscription: PushSubscription) -> None:
        await self._session.merge(PushSubscriptionMapper.to_model(subscription))
        await self._session.flush()
