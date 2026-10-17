from dishka import Provider, Scope, provide

from app.application.health.check_readiness import CheckReadiness
from app.application.health.dto import ReadinessProbes
from app.infrastructure.db.health import DatabaseHealthProbe, NotificationLagProbe
from app.infrastructure.redis.client import RedisHealthProbe


class ObservabilityProvider(Provider):
    scope = Scope.APP

    @provide
    def readiness_probes(
        self,
        database: DatabaseHealthProbe,
        redis: RedisHealthProbe,
        notifications: NotificationLagProbe,
    ) -> ReadinessProbes:
        # Postgres is the source of truth; Redis only powers fail-open features.
        # A stuck notification queue (worker down) degrades, never stops, the api.
        return ReadinessProbes(
            critical={"postgres": database},
            auxiliary={"redis": redis, "notifications": notifications},
        )

    @provide
    def check_readiness(self, probes: ReadinessProbes) -> CheckReadiness:
        return CheckReadiness(probes)
