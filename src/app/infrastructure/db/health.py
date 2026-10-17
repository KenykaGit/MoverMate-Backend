from datetime import timedelta

from sqlalchemy import Interval, func, select, text, type_coerce
from sqlalchemy.ext.asyncio import AsyncEngine

from app.application.interfaces.health import AbstractHealthProbe
from app.domain.notifications.notification import NotificationStatus
from app.infrastructure.db.models.notification import NotificationModel


class DatabaseHealthProbe(AbstractHealthProbe):
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def check(self) -> None:
        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))


class NotificationLagError(Exception):
    """A due notification has waited too long: the worker is down or overloaded."""


class NotificationLagProbe(AbstractHealthProbe):
    """Fails while the oldest due notification waits longer than `max_lag`.

    The api does not send notifications, but it is where /health/ready is: a
    stopped worker shows up there as `degraded` instead of as silent users.
    """

    def __init__(self, engine: AsyncEngine, max_lag: timedelta) -> None:
        self._engine = engine
        self._max_lag = max_lag

    async def check(self) -> None:
        # Database time on both sides: a skewed api clock cannot fake a lag.
        # min() over the partial index of pending rows is a single index lookup.
        async with self._engine.connect() as connection:
            lag = await connection.scalar(
                select(
                    type_coerce(func.now() - func.min(NotificationModel.scheduled_at), Interval())
                ).where(NotificationModel.status == NotificationStatus.PENDING.value)
            )
        if lag is not None and lag > self._max_lag:
            raise NotificationLagError(f"The oldest due notification waits {lag}")
