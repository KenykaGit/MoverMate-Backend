"""Prometheus metrics. Labels are bounded: route templates and enums, never ids."""

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds", "HTTP request latency", ["method", "route"]
)
REDIS_ERRORS = Counter(
    "redis_errors_total", "Failed Redis operations (served in fail-open mode)", ["op"]
)
RATE_LIMITED = Counter("rate_limited_total", "Requests rejected by the rate limiter", ["scope"])


def observe_http_request(*, method: str, route: str, status: int, duration: float) -> None:
    HTTP_REQUESTS.labels(method, route, str(status)).inc()
    HTTP_REQUEST_DURATION.labels(method, route).observe(duration)


def record_rate_limited(scope: str) -> None:
    RATE_LIMITED.labels(scope).inc()


def render_latest() -> tuple[bytes, str]:
    """The exposition body and its content type."""
    return generate_latest(), CONTENT_TYPE_LATEST
