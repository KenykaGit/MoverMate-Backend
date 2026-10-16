import pytest

from app.application.health.check_readiness import CheckReadiness
from app.application.health.dto import Readiness, ReadinessProbes
from tests.conftest import LogEvents
from tests.fakes import FakeHealthProbe


def readiness(*, postgres: FakeHealthProbe, redis: FakeHealthProbe) -> CheckReadiness:
    probes = ReadinessProbes(critical={"postgres": postgres}, auxiliary={"redis": redis})
    return CheckReadiness(probes, timeout_seconds=0.05)


async def test_all_healthy() -> None:
    result = await readiness(postgres=FakeHealthProbe(), redis=FakeHealthProbe())()

    assert result.status is Readiness.OK
    assert result.checks == {"postgres": Readiness.OK, "redis": Readiness.OK}


async def test_auxiliary_outage_degrades(log_events: LogEvents) -> None:
    result = await readiness(postgres=FakeHealthProbe(), redis=FakeHealthProbe(healthy=False))()

    assert result.status is Readiness.DEGRADED
    assert result.checks == {"postgres": Readiness.OK, "redis": Readiness.DEGRADED}
    [event] = [e for e in log_events() if e["event"] == "readiness_check_failed"]
    assert event["dependency"] == "redis"


@pytest.mark.parametrize("redis_healthy", [True, False])
async def test_critical_outage_makes_the_service_unavailable(redis_healthy: bool) -> None:
    result = await readiness(
        postgres=FakeHealthProbe(healthy=False), redis=FakeHealthProbe(healthy=redis_healthy)
    )()

    assert result.status is Readiness.UNAVAILABLE
    assert result.checks["postgres"] is Readiness.UNAVAILABLE


async def test_a_hanging_dependency_counts_as_down() -> None:
    result = await readiness(postgres=FakeHealthProbe(), redis=FakeHealthProbe(hangs=True))()

    assert result.checks["redis"] is Readiness.DEGRADED
