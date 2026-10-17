from dishka import Provider, Scope, provide

from app.application.health.check_readiness import CheckReadiness
from app.application.health.dto import ReadinessProbes
from app.application.interfaces.health import AbstractHealthProbe
from app.config import LlmSettings
from app.infrastructure.db.health import DatabaseHealthProbe, NotificationLagProbe
from app.infrastructure.llm.recognizer import LlmCallStatus
from app.infrastructure.redis.client import RedisHealthProbe


class ObservabilityProvider(Provider):
    scope = Scope.APP

    @provide
    def readiness_probes(
        self,
        database: DatabaseHealthProbe,
        redis: RedisHealthProbe,
        notifications: NotificationLagProbe,
        llm: LlmCallStatus,
        llm_settings: LlmSettings,
    ) -> ReadinessProbes:
        # Postgres is the source of truth; Redis only powers fail-open features.
        # A stuck notification queue (worker down) degrades, never stops, the api.
        # So does a failing LLM (photo scanning only); switched off, it is not checked.
        auxiliary: dict[str, AbstractHealthProbe] = {"redis": redis, "notifications": notifications}
        if llm_settings.api_key is not None:
            auxiliary["llm"] = llm
        return ReadinessProbes(critical={"postgres": database}, auxiliary=auxiliary)

    @provide
    def check_readiness(self, probes: ReadinessProbes) -> CheckReadiness:
        return CheckReadiness(probes)
