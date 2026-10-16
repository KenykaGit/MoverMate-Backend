import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.infrastructure.logging import setup_logging

TESTS_ROOT = Path(__file__).parent
INTEGRATION_ROOT = TESTS_ROOT / "integration"

type LogEvents = Callable[[], list[dict[str, Any]]]

_STANDARD_RECORD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Everything under tests/integration needs Docker: mark it, so that
    `pytest -m "not integration"` runs the fast suite only."""
    for item in items:
        if INTEGRATION_ROOT in Path(item.path).parents:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session", autouse=True)
def _logging() -> None:
    # The same structlog pipeline as in production, so tests see the same events.
    setup_logging("WARNING", json_logs=False)
    # Output is pytest's business (caplog, "Captured log" on failure): a stdout
    # handler would outlive pytest's capture streams.
    logging.getLogger().handlers.clear()


@pytest.fixture
def log_events(caplog: pytest.LogCaptureFixture) -> LogEvents:
    """Returns the structured events logged so far, as dicts with an "event" key.

    structlog events arrive as dicts; stdlib records (the application layer)
    are converted together with their `extra` fields.
    """
    caplog.set_level(logging.DEBUG)

    def collect() -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for record in caplog.records:
            if isinstance(record.msg, dict):
                events.append(dict(record.msg))
                continue
            extra = {k: v for k, v in vars(record).items() if k not in _STANDARD_RECORD_ATTRS}
            events.append({"event": record.getMessage(), "level": record.levelname.lower()} | extra)
        return events

    return collect
