"""Raw-SQL inserts with valid defaults: constraints are tested one column at a time.

Ids come from the domain generator (UUIDv7), as in production. Unique strings use
`secrets`: the leading hex digits of a UUIDv7 are a timestamp, not random.
"""

import secrets
from datetime import UTC, datetime
from itertools import count
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.common.ids import new_id
from app.domain.inventory.value_objects import QrToken

NOW = datetime(2026, 7, 1, tzinfo=UTC)
_box_numbers = count(1)


def row_id() -> str:
    return str(new_id())


async def insert(connection: AsyncConnection, table: str, values: dict[str, Any]) -> None:
    columns = ", ".join(values)
    params = ", ".join(f":{name}" for name in values)
    # Table and column names are literals of the tests; values are bound parameters.
    statement = f"INSERT INTO {table} ({columns}) VALUES ({params})"  # noqa: S608
    await connection.execute(text(statement), values)


async def insert_user(connection: AsyncConnection, **overrides: Any) -> str:
    values = {
        "id": row_id(),
        "username": secrets.token_hex(8),
        "password_hash": "hash",
        "is_active": True,
    } | overrides
    await insert(connection, "users", values)
    return str(values["id"])


async def insert_project(connection: AsyncConnection, **overrides: Any) -> str:
    """A project with its owner as the first member."""
    owner_id = overrides.pop("owner_id", None) or await insert_user(connection)
    values = {
        "id": row_id(),
        "owner_id": owner_id,
        "name": "Move",
        "moving_date": NOW.date(),
        "qr_code_strategy": "PROTECTED",
        "time_zone": "Europe/Moscow",
        "is_active": True,
    } | overrides
    await insert(connection, "projects", values)
    await add_member(connection, str(values["id"]), owner_id)
    return str(values["id"])


async def add_member(connection: AsyncConnection, project_id: str, user_id: str) -> None:
    await insert(
        connection, "project_collaborators", {"project_id": project_id, "user_id": user_id}
    )


async def insert_room(connection: AsyncConnection, project_id: str, **overrides: Any) -> str:
    values = {
        "id": row_id(),
        "project_id": project_id,
        "kind": "SOURCE",
        "name": secrets.token_hex(8),
        "is_active": True,
    } | overrides
    await insert(connection, "rooms", values)
    return str(values["id"])


async def insert_box(connection: AsyncConnection, project_id: str, **overrides: Any) -> str:
    room_id = overrides.pop("destination_room_id", None) or await insert_room(
        connection, project_id, kind="DESTINATION"
    )
    values = {
        "id": row_id(),
        "project_id": project_id,
        "number": next(_box_numbers),
        "qr_token": QrToken.generate().value,
        "destination_room_id": room_id,
        "created_by_id": await insert_user(connection),
        "is_fragile": False,
        "status": "PACKING",
        "status_changed_at": NOW,
        "is_active": True,
    } | overrides
    await insert(connection, "boxes", values)
    return str(values["id"])


async def insert_item(connection: AsyncConnection, project_id: str, **overrides: Any) -> str:
    room_id = overrides.pop("source_room_id", None) or await insert_room(connection, project_id)
    values = {
        "id": row_id(),
        "project_id": project_id,
        "source_room_id": room_id,
        "box_id": None,
        "name": "Kettle",
        "quantity": 1,
        "priority": 2,
        "state": "TO_PACK",
        "is_active": True,
    } | overrides
    await insert(connection, "items", values)
    return str(values["id"])


async def insert_task(connection: AsyncConnection, project_id: str, **overrides: Any) -> str:
    values = {
        "id": row_id(),
        "project_id": project_id,
        "title": "Order movers",
        "phase": "BEFORE",
        "is_done": False,
        "done_at": None,
        "is_active": True,
    } | overrides
    await insert(connection, "tasks", values)
    return str(values["id"])


async def insert_notification(
    connection: AsyncConnection, project_id: str, **overrides: Any
) -> str:
    """A pending TASK_ASSIGNED notification unless overridden."""
    if "task_id" not in overrides and "box_id" not in overrides:
        overrides["task_id"] = await insert_task(connection, project_id)
    values = {
        "id": row_id(),
        "project_id": project_id,
        "user_id": await insert_user(connection),
        "type": "TASK_ASSIGNED",
        "task_id": None,
        "box_id": None,
        "reminder_offset_hours": None,
        "scheduled_at": NOW,
        "status": "PENDING",
        "attempts": 0,
    } | overrides
    await insert(connection, "notifications", values)
    return str(values["id"])
