import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import structlog
from dishka import AsyncContainer
from dishka.integrations.fastapi import setup_dishka
from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.application.seed.dto import SeedTestUsersDTO
from app.application.seed.seed_test_users import SeedTestUsers
from app.presentation.http.errors import (
    ERROR_RESPONSES,
    REQUEST_ID_HEADER,
    register_error_handlers,
)
from app.presentation.http.idempotency import REPLAY_HEADER
from app.presentation.http.middleware import RequestContextMiddleware
from app.presentation.http.rate_limit import api_rate_limit
from app.presentation.http.routes import (
    auth,
    boxes,
    health,
    items,
    metrics,
    projects,
    push,
    rooms,
    summaries,
    tasks,
    users,
)
from app.presentation.http.settings import HttpSettings

logger = structlog.get_logger(__name__)

API_PREFIX = "/api/v1"


async def seed_test_users(container: AsyncContainer, settings: HttpSettings) -> None:
    """Resolve the use case by hand in its own request scope, as a route would.

    A failure is logged, not raised: test data must never keep the api from starting.
    """
    try:
        async with container() as scope:
            seed = await scope.get(SeedTestUsers)
            await seed(
                SeedTestUsersDTO()
                if settings.seed_test_password is None
                else SeedTestUsersDTO(password=settings.seed_test_password)
            )
    except Exception:  # startup must survive e.g. a not yet migrated database
        logger.exception("test_users_seed_failed")


def create_app(container: AsyncContainer, settings: HttpSettings) -> FastAPI:
    """Build the HTTP app around a configured container; the app owns it."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("startup", version=settings.version)
        # In the background: filling a heavy account takes a while, and the api must answer
        # its health checks meanwhile.
        seeding = (
            asyncio.create_task(seed_test_users(container, settings))
            if settings.seed_test_users
            else None
        )
        yield
        if seeding is not None:
            seeding.cancel()
            with suppress(asyncio.CancelledError):
                await seeding
        await container.close()  # disposes the engine, closes Redis
        logger.info("shutdown")

    app = FastAPI(title=settings.title, version=settings.version, lifespan=lifespan)
    setup_dishka(container, app)

    # The last added middleware is the outermost one.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            REQUEST_ID_HEADER,
            REPLAY_HEADER,
            "Retry-After",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
        ],
    )
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)

    # Operational endpoints: unversioned and not rate limited.
    app.include_router(health.router)
    app.include_router(metrics.router)

    api_v1 = APIRouter(dependencies=[Depends(api_rate_limit)], responses=ERROR_RESPONSES)
    routers = (
        auth.router,
        users.router,
        projects.router,
        summaries.router,
        rooms.router,
        boxes.router,
        items.router,
        tasks.templates_router,
        tasks.router,
        push.router,
    )
    for router in routers:
        api_v1.include_router(router)
    app.include_router(api_v1, prefix=API_PREFIX)
    return app
