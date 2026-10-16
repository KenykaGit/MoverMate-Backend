"""Structured logging: structlog and stdlib (application layer, uvicorn, SQLAlchemy)
go through one renderer, so every line is JSON in prod and carries `request_id`."""

import logging
import sys

import structlog

_SHARED_PROCESSORS: list[structlog.types.Processor] = [
    structlog.contextvars.merge_contextvars,  # request_id, user_id
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    # `logger.info("event", extra={...})` from the application layer, which
    # uses the stdlib only, ends up as fields of the event.
    structlog.stdlib.ExtraAdder(),
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
]


def setup_logging(level: str, *, json_logs: bool) -> None:
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*_SHARED_PROCESSORS, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_SHARED_PROCESSORS,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # uvicorn installs its own handlers: route them through ours. The access log
    # is written by RequestContextMiddleware (with request_id and route).
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
