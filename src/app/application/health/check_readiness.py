import asyncio
import logging

from app.application.health.dto import Readiness, ReadinessDTO, ReadinessProbes
from app.application.interfaces.health import AbstractHealthProbe

logger = logging.getLogger(__name__)

DEFAULT_PROBE_TIMEOUT_SECONDS = 2.0


class CheckReadiness:
    def __init__(
        self, probes: ReadinessProbes, timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS
    ) -> None:
        self._probes = probes
        self._timeout = timeout_seconds

    async def __call__(self) -> ReadinessDTO:
        """Probe all dependencies concurrently, each with its own timeout."""
        all_probes = {**self._probes.critical, **self._probes.auxiliary}
        results = await asyncio.gather(
            *(self._is_healthy(name, probe) for name, probe in all_probes.items())
        )
        healthy = dict(zip(all_probes, results, strict=True))

        checks = {
            name: Readiness.OK if healthy[name] else Readiness.UNAVAILABLE
            for name in self._probes.critical
        } | {
            name: Readiness.OK if healthy[name] else Readiness.DEGRADED
            for name in self._probes.auxiliary
        }
        if any(checks[name] is not Readiness.OK for name in self._probes.critical):
            status = Readiness.UNAVAILABLE
        elif any(check is not Readiness.OK for check in checks.values()):
            status = Readiness.DEGRADED
        else:
            status = Readiness.OK
        return ReadinessDTO(status=status, checks=checks)

    async def _is_healthy(self, name: str, probe: AbstractHealthProbe) -> bool:
        try:
            async with asyncio.timeout(self._timeout):
                await probe.check()
        except Exception as error:  # noqa: BLE001  # any failure means "not ready"
            logger.warning(
                "readiness_check_failed", extra={"dependency": name, "error": repr(error)}
            )
            return False
        return True
