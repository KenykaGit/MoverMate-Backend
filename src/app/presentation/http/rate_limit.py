"""Rate-limit dependencies. Fail-open: when the limiter's storage is down,
requests pass and the outage shows up in redis_errors_total."""

from collections.abc import Awaitable, Callable

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import Request

from app.application.interfaces.rate_limiter import AbstractRateLimiter
from app.infrastructure.metrics.prometheus import record_rate_limited
from app.presentation.http.errors import RateLimitedError
from app.presentation.http.middleware import add_response_headers
from app.presentation.http.settings import RateLimitPolicy

type RateLimitDependency = Callable[..., Awaitable[None]]


def rate_limit(scope: str, limit_of: Callable[[RateLimitPolicy], int]) -> RateLimitDependency:
    """A dependency counting requests per client IP in its own `scope`.

    Scopes stack: a route under both `api` and `auth` is counted in both.
    """

    @inject
    async def dependency(
        request: Request,
        limiter: FromDishka[AbstractRateLimiter],
        policy: FromDishka[RateLimitPolicy],
    ) -> None:
        limit = limit_of(policy)
        if limit <= 0:
            return
        # The real peer: behind nginx, uvicorn's --proxy-headers takes it from
        # X-Forwarded-For, which nginx overwrites (never appends), so a client
        # cannot spoof it to dodge the limit.
        client_ip = request.client.host if request.client else "unknown"
        decision = await limiter.hit(
            f"{scope}:{client_ip}", limit=limit, window_seconds=policy.window_seconds
        )
        if decision is None:
            return

        headers = {
            "X-RateLimit-Limit": str(decision.limit),
            "X-RateLimit-Remaining": str(decision.remaining),
        }
        # With stacked scopes, the route-level one (applied last) sets the headers.
        add_response_headers(request, headers)
        if not decision.allowed:
            record_rate_limited(scope)
            raise RateLimitedError({**headers, "Retry-After": str(decision.retry_after)})

    return dependency


api_rate_limit = rate_limit("api", lambda policy: policy.api_per_window)
auth_rate_limit = rate_limit("auth", lambda policy: policy.auth_per_window)
scan_rate_limit = rate_limit("scan", lambda policy: policy.scan_per_window)
