"""Request context: request id, access log and HTTP metrics (pure ASGI middleware)."""

import re
import time
from uuid import uuid4

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.infrastructure.metrics.prometheus import observe_http_request
from app.presentation.http.errors import REQUEST_ID_HEADER

logger = structlog.get_logger("app.access")

# A caller's id is reused only if it is safe to log and to echo back.
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
UNMATCHED_ROUTE = "unmatched"
_EXTRA_HEADERS_STATE = "response_headers"


def add_response_headers(request: Request, headers: dict[str, str]) -> None:
    """Attach headers to whatever response this request ends with.

    Unlike FastAPI's `Response` parameter, this also covers error responses
    built by exception handlers (e.g. X-RateLimit-* on a 401).
    """
    stash: dict[str, str] = request.scope.setdefault("state", {}).setdefault(
        _EXTRA_HEADERS_STATE, {}
    )
    stash.update(headers)


def route_template(scope: Scope) -> str:
    """`/api/v1/items/<uuid>` -> `/api/v1/items/{item_id}`: bounded metric labels.

    Built from the raw path and the matched path params; unmatched paths
    (scanners, typos) collapse into one label.
    """
    if scope.get("route") is None:
        return UNMATCHED_ROUTE
    names = {str(value): name for name, value in scope.get("path_params", {}).items()}
    return "/".join(
        f"{{{names[part]}}}" if part in names else part for part in scope["path"].split("/")
    )


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(REQUEST_ID_HEADER)
        is_valid = incoming is not None and _VALID_REQUEST_ID.fullmatch(incoming) is not None
        request_id = incoming if is_valid and incoming is not None else uuid4().hex
        # Shared with the Request objects of handlers via the scope's state dict.
        scope.setdefault("state", {})["request_id"] = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        status_code = 500  # if the app fails before starting a response
        start = time.perf_counter()

        async def send_with_context_headers(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                extra = scope["state"].get(_EXTRA_HEADERS_STATE, {})
                for name, value in {REQUEST_ID_HEADER: request_id, **extra}.items():
                    if name not in headers:
                        headers.append(name, value)
            await send(message)

        try:
            await self.app(scope, receive, send_with_context_headers)
        except Exception:
            logger.exception("unhandled_error", method=scope["method"], path=scope["path"])
            raise
        finally:
            duration = time.perf_counter() - start
            route = route_template(scope)
            observe_http_request(
                method=scope["method"], route=route, status=status_code, duration=duration
            )
            log = logger.warning if status_code >= 500 else logger.info  # noqa: PLR2004
            log(
                "http_request",
                method=scope["method"],
                path=scope["path"],
                route=route,
                status=status_code,
                duration_ms=round(duration * 1000, 2),
            )
