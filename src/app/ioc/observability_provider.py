from dishka import Provider, Scope, provide

from app.application.health.check_readiness import CheckReadiness
from app.application.health.dto import ReadinessProbes
from app.infrastructure.db.health import DatabaseHealthProbe
from app.infrastructure.redis.client import RedisHealthProbe


class ObservabilityProvider(Provider):
    scope = Scope.APP

    @provide
    def readiness_probes(
        self, database: DatabaseHealthProbe, redis: RedisHealthProbe
    ) -> ReadinessProbes:
        # Postgres is the source of truth; Redis only powers fail-open features.
        return ReadinessProbes(critical={"postgres": database}, auxiliary={"redis": redis})

    @provide
    def check_readiness(self, probes: ReadinessProbes) -> CheckReadiness:
        return CheckReadiness(probes)
