"""Human-readable `message` of log events (what Sage shows in its result list)."""

import ast
import json
import logging
from pathlib import Path
from typing import Any

import pytest
import structlog

from app.infrastructure import log_messages
from app.infrastructure.log_messages import EVENT_MESSAGES, add_message, render_message
from app.infrastructure.logging import build_formatter

SRC = Path(__file__).parents[3] / "src" / "app"
_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}


def _is_log_call(func: ast.expr) -> bool:
    """`logger.info(...)`, or `log(...)` after `log = logger.warning if ... else ...`."""
    if isinstance(func, ast.Name):
        return func.id == "log"
    return (
        isinstance(func, ast.Attribute)
        and func.attr in _LOG_METHODS
        and isinstance(func.value, ast.Name)
        and func.value.id == "logger"
    )


def logged_event_names() -> set[str]:
    """Literal event names of every `logger.<level>("event", ...)` call in the app.

    A computed name (`event = "a" if x else "b"`) is checked by its literals.
    """
    names: set[str] = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(node, ast.Call) and node.args and _is_log_call(node.func)):
                continue
            first = node.args[0]
            literals = [first] if isinstance(first, ast.Constant) else []
            if isinstance(first, ast.Name):  # resolve `event = "x" if ... else "y"`
                literals = [
                    const
                    for assign in ast.walk(ast.parse(path.read_text()))
                    if isinstance(assign, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == first.id for t in assign.targets)
                    for const in ast.walk(assign.value)
                    if isinstance(const, ast.Constant) and isinstance(const.value, str)
                ]
            names.update(c.value for c in literals if isinstance(c.value, str))
    return names


def test_every_logged_event_has_a_message() -> None:
    names = logged_event_names()

    assert "project_created" in names  # the scan itself works
    assert sorted(names - EVENT_MESSAGES.keys()) == []


def test_no_message_for_an_event_nobody_logs() -> None:
    assert sorted(EVENT_MESSAGES.keys() - logged_event_names()) == []


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        (
            {"username": "alice", "user_id": "0191", "project_name": "Дача", "rooms": 2},
            'user alice created project "Дача" with 2 rooms',
        ),
        # Seeding at startup or a use case outside a request: no username yet.
        (
            {"user_id": "0191", "project_name": "Дача", "rooms": 0},
            'user 0191 created project "Дача" with 0 rooms',
        ),
        ({"project_name": "Дача"}, 'anonymous created project "Дача" with ? rooms'),
    ],
)
def test_names_who_did_it(fields: dict[str, Any], expected: str) -> None:
    assert render_message("project_created", fields) == expected


def test_lists_read_as_text() -> None:
    message = render_message(
        "item_updated",
        {"username": "bob", "item_name": "Чайник", "fields": ["name", "quantity"]},
    )

    assert message == 'user bob updated item "Чайник": name, quantity'


def test_access_log_names_the_error_code() -> None:
    fields = {"method": "PUT", "path": "/api/v1/users/me/username", "duration_ms": 6.1}

    ok = render_message("http_request", fields | {"username": "alice", "status": 200})
    failed = render_message(
        "http_request", fields | {"status": 409, "error_code": "username_already_taken"}
    )

    assert ok == "user alice: PUT /api/v1/users/me/username -> 200 in 6.1 ms"
    assert failed == (
        "anonymous: PUT /api/v1/users/me/username -> 409 username_already_taken in 6.1 ms"
    )


def test_unknown_events_and_set_messages_are_left_alone() -> None:
    assert render_message("Started server process", {}) is None
    assert "message" not in add_message(None, "info", {"event": "Started server process"})
    assert add_message(None, "info", {"event": "user_logged_in", "message": "kept"}) == {
        "event": "user_logged_in",
        "message": "kept",
    }


def test_a_broken_template_never_loses_the_event(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_: object) -> str:
        raise RuntimeError

    monkeypatch.setitem(log_messages.EVENT_MESSAGES, "user_logged_in", broken)

    assert add_message(None, "info", {"event": "user_logged_in"}) == {"event": "user_logged_in"}


def test_json_line_carries_the_message_and_the_context() -> None:
    """The line Vector ships to Sage: stdlib `extra` plus the request context."""
    record = logging.makeLogRecord(
        {
            "name": "app.application.projects.create_project",
            "levelno": logging.INFO,
            "levelname": "INFO",
            "msg": "project_created",
            "project_name": "Дача",
            "rooms": 3,
        }
    )

    with structlog.contextvars.bound_contextvars(
        request_id="f111", user_id="0191", username="alice"
    ):
        line = json.loads(build_formatter(json_logs=True).format(record))

    assert line["event"] == "project_created"
    assert line["message"] == 'user alice created project "Дача" with 3 rooms'
    assert (line["request_id"], line["username"], line["rooms"]) == ("f111", "alice", 3)
