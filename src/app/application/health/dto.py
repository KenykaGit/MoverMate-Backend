from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from app.application.interfaces.health import AbstractHealthProbe


class Readiness(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    """Only auxiliary dependencies are down: the service still serves traffic."""
    UNAVAILABLE = "unavailable"
    """A critical dependency is down: take the instance out of rotation."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadinessProbes:
    critical: Mapping[str, AbstractHealthProbe]
    """Without these the service cannot work (the source of truth)."""
    auxiliary: Mapping[str, AbstractHealthProbe] = field(default_factory=dict)
    """Features degrade without these, but the main scenario keeps working."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadinessDTO:
    status: Readiness
    checks: dict[str, Readiness]
