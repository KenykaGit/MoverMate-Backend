# syntax=docker/dockerfile:1
# ---- build: resolve dependencies from the lockfile into /app/.venv ----
FROM python:3.14.8-slim-trixie AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
# Dependencies first: this layer is cached until pyproject.toml/uv.lock change.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Then the project itself, installed as a regular (non-editable) package.
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---- runtime: no build tools, no uv, no sources outside the venv, non-root ----
FROM python:3.14.8-slim-trixie AS runtime

RUN groupadd --system app && useradd --system --gid app --no-create-home app \
    # The worker keeps its VAPID key here (a named volume copies this ownership).
    && install -d -o app -g app -m 700 /var/lib/app/vapid

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations ./migrations

ARG APP_VERSION=dev
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_VERSION=${APP_VERSION}

USER app
EXPOSE 8000

# No curl in slim images: probe with the Python we already have.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]

# The same image runs migrations: `alembic upgrade head` (the compose `migrate` service),
# and the notification worker on taskiq (the compose `worker` and `scheduler` services).
# FORWARDED_ALLOW_IPS: proxies trusted to set X-Forwarded-For (the rate limiter keys on
# the real client IP). The access log is written by the app with request_id and route.
CMD ["sh", "-c", "exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=\"${FORWARDED_ALLOW_IPS:-127.0.0.1}\" --no-access-log"]
