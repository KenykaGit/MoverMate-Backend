"""Liveness and readiness: unversioned and never rate limited."""

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Response, status

from app.application.health.check_readiness import CheckReadiness
from app.application.health.dto import Readiness
from app.presentation.http.schemas.health import LiveResponse, ReadyResponse
from app.presentation.http.settings import HttpSettings

router = APIRouter(prefix="/health", tags=["health"], route_class=DishkaRoute)


@router.get("/live")
async def live() -> LiveResponse:
    """The process is up. Touches no dependency: restarting would not fix them."""
    return LiveResponse()


@router.get(
    "/ready",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadyResponse,
            "description": "PostgreSQL is unavailable",
        }
    },
)
async def ready(
    response: Response,
    check_readiness: FromDishka[CheckReadiness],
    settings: FromDishka[HttpSettings],
) -> ReadyResponse:
    """`degraded` (200) when only Redis is down: the instance still serves traffic,
    so it must not be taken out of rotation. `unavailable` (503) without Postgres."""
    readiness = await check_readiness()
    if readiness.status is Readiness.UNAVAILABLE:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(status=readiness.status, version=settings.version, checks=readiness.checks)
