"""The database guards the rules of the moving domain too (docs/db-design.md, section 11).

State machines live twice: as matrices in the domain and as triggers in the
database. The tests below try every (from, to) pair and require both to agree.
"""

from collections.abc import Awaitable, Callable
from itertools import product
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.inventory.box import BOX_TRANSITIONS, BoxStatus
from app.domain.inventory.item import ITEM_TRANSITIONS, ItemState
from app.domain.notifications.notification import NOTIFICATION_TRANSITIONS, NotificationStatus
from app.infrastructure.db.errors import violated_constraint
from app.infrastructure.db.models.box import (
    ACTIVE_BOX_NUMBER_UNIQUE_INDEX,
    BOX_STATUS_TRANSITION_CONSTRAINT,
)
from app.infrastructure.db.models.item import ITEM_STATE_TRANSITION_CONSTRAINT
from app.infrastructure.db.models.notification import NOTIFICATION_STATUS_TRANSITION_CONSTRAINT
from app.infrastructure.db.models.room import ACTIVE_ROOM_NAME_UNIQUE_INDEX
from tests.integration.migrations.rows import (
    NOW,
    add_member,
    insert,
    insert_box,
    insert_item,
    insert_notification,
    insert_project,
    insert_room,
    insert_task,
    insert_user,
    row_id,
)


@pytest.fixture
async def project(connection: AsyncConnection) -> str:
    return await insert_project(connection)


async def violation(action: Awaitable[Any], connection: AsyncConnection) -> str | None:
    """The constraint `action` violates, or None if it succeeds (inside a SAVEPOINT)."""
    try:
        async with connection.begin_nested():
            await action
    except IntegrityError as error:
        return violated_constraint(error) or "unnamed"
    return None


async def update(connection: AsyncConnection, sql: str, **params: Any) -> None:
    await connection.execute(text(sql), params)


class TestTransitionTriggersMatchTheDomain:
    async def test_box_status(self, connection: AsyncConnection, project: str) -> None:
        mismatches = []
        for source, target in product(BoxStatus, BoxStatus):
            box = await insert_box(connection, project, status=source.value)
            found = await violation(
                update(connection, "UPDATE boxes SET status = :s WHERE id = :id", s=target, id=box),
                connection,
            )
            allowed = source is target or (source, target) in BOX_TRANSITIONS
            expected = None if allowed else BOX_STATUS_TRANSITION_CONSTRAINT
            if found != expected:
                mismatches.append((source, target, found))

        assert mismatches == []

    async def test_item_state(self, connection: AsyncConnection, project: str) -> None:
        mismatches = []
        for source, target in product(ItemState, ItemState):
            first = await insert_box(connection, project)
            second = await insert_box(connection, project)
            item = await insert_item(
                connection,
                project,
                state=source.value,
                box_id=first if source is ItemState.IN_BOX else None,
            )
            # Into a box means into another box: IN_BOX -> IN_BOX is a move.
            box_id = second if target is ItemState.IN_BOX else None
            found = await violation(
                update(
                    connection,
                    "UPDATE items SET state = :s, box_id = :box WHERE id = :id",
                    s=target,
                    box=box_id,
                    id=item,
                ),
                connection,
            )
            allowed = source is target or (source, target) in ITEM_TRANSITIONS
            expected = None if allowed else ITEM_STATE_TRANSITION_CONSTRAINT
            if found != expected:
                mismatches.append((source, target, found))

        assert mismatches == []

    async def test_notification_status(self, connection: AsyncConnection, project: str) -> None:
        mismatches = []
        for source, target in product(NotificationStatus, NotificationStatus):
            notification = await insert_notification(connection, project, status=source.value)
            found = await violation(
                update(
                    connection,
                    "UPDATE notifications SET status = :s WHERE id = :id",
                    s=target,
                    id=notification,
                ),
                connection,
            )
            allowed = source is target or (source, target) in NOTIFICATION_TRANSITIONS
            expected = None if allowed else NOTIFICATION_STATUS_TRANSITION_CONSTRAINT
            if found != expected:
                mismatches.append((source, target, found))

        assert mismatches == []


type Insert = Callable[[AsyncConnection, str], Awaitable[Any]]


def _with(insert_row: Callable[..., Awaitable[Any]], **values: Any) -> Insert:
    return lambda connection, project: insert_row(connection, project, **values)


@pytest.mark.parametrize(
    ("insert_row", "constraint"),
    [
        (_with(insert_room, kind="HALLWAY"), "ck_rooms_kind"),
        (_with(insert_box, number=0), "ck_boxes_number_positive"),
        (_with(insert_box, qr_token="short"), "ck_boxes_qr_token_length"),
        (_with(insert_box, status="LOST"), "ck_boxes_status"),
        (_with(insert_item, state="IN_BOX"), "ck_items_box_id_iff_in_box"),
        (_with(insert_item, quantity=0), "ck_items_quantity_range"),
        (_with(insert_item, priority=4), "ck_items_priority_range"),
        (_with(insert_item, state="BROKEN"), "ck_items_state"),
        (_with(insert_task, is_done=True), "ck_tasks_done_at_iff_done"),
        (_with(insert_task, done_at=NOW), "ck_tasks_done_at_iff_done"),
        (_with(insert_task, phase="SOMETIME"), "ck_tasks_phase"),
        (
            _with(insert_notification, type="TASK_REMINDER"),
            "ck_notifications_offset_iff_task_reminder",
        ),
        (
            _with(insert_notification, reminder_offset_hours=24),
            "ck_notifications_offset_iff_task_reminder",
        ),
        (
            _with(insert_notification, type="BOX_CONTENTS_REMINDER"),
            "ck_notifications_target_matches_type",
        ),
        (
            # A moving date reminder is about the project: no task, no box.
            _with(insert_notification, type="MOVING_DATE_REMINDER", reminder_offset_days=7),
            "ck_notifications_target_matches_type",
        ),
        (
            _with(insert_notification, type="MOVING_DATE_REMINDER", task_id=None),
            "ck_notifications_offset_days_iff_moving_date_reminder",
        ),
        (
            _with(insert_notification, reminder_offset_days=7),
            "ck_notifications_offset_days_iff_moving_date_reminder",
        ),
        (_with(insert_notification, status="LATE"), "ck_notifications_status"),
        (_with(insert_notification, attempts=-1), "ck_notifications_attempts_non_negative"),
    ],
)
async def test_check_constraints(
    connection: AsyncConnection, project: str, insert_row: Insert, constraint: str
) -> None:
    assert await violation(insert_row(connection, project), connection) == constraint


async def test_project_qr_code_strategy(connection: AsyncConnection) -> None:
    found = await violation(insert_project(connection, qr_code_strategy="SECRET"), connection)

    assert found == "ck_projects_qr_code_strategy"


async def test_reminder_offset_range(connection: AsyncConnection, project: str) -> None:
    task = await insert_task(connection, project)

    found = await violation(
        insert(connection, "task_reminders", {"task_id": task, "offset_hours": 0}), connection
    )

    assert found == "ck_task_reminders_offset_hours_range"


async def test_moving_reminder_offset_range(connection: AsyncConnection, project: str) -> None:
    found = await violation(
        insert(connection, "project_reminders", {"project_id": project, "offset_days": 0}),
        connection,
    )

    assert found == "ck_project_reminders_offset_days_range"


async def test_audit_entity_type_follows_the_action(
    connection: AsyncConnection, project: str
) -> None:
    values = {
        "id": row_id(),
        "project_id": project,
        "entity_type": "ITEM",
        "entity_id": row_id(),
        "action": "BOX_CREATED",
        "details": "{}",
        "created_at": NOW,
    }

    found = await violation(insert(connection, "audit_logs", values), connection)

    assert found == "ck_audit_logs_entity_type_of_action"


class TestObjectsStayInTheirProject:
    async def test_item_cannot_use_a_room_of_another_project(
        self, connection: AsyncConnection, project: str
    ) -> None:
        foreign_room = await insert_room(connection, await insert_project(connection))

        found = await violation(
            insert_item(connection, project, source_room_id=foreign_room), connection
        )

        assert found == "fk_items_source_room_id_rooms"

    async def test_item_cannot_go_into_a_box_of_another_project(
        self, connection: AsyncConnection, project: str
    ) -> None:
        foreign_box = await insert_box(connection, await insert_project(connection))

        found = await violation(
            insert_item(connection, project, state="IN_BOX", box_id=foreign_box), connection
        )

        assert found == "fk_items_box_id_boxes"

    async def test_box_cannot_go_to_a_room_of_another_project(
        self, connection: AsyncConnection, project: str
    ) -> None:
        foreign_room = await insert_room(
            connection, await insert_project(connection), kind="DESTINATION"
        )

        found = await violation(
            insert_box(connection, project, destination_room_id=foreign_room), connection
        )

        assert found == "fk_boxes_destination_room_id_rooms"


class TestAssignees:
    async def test_must_be_members_of_the_project(
        self, connection: AsyncConnection, project: str
    ) -> None:
        task = await insert_task(connection, project)
        stranger = await insert_user(connection)
        values = {"task_id": task, "project_id": project, "user_id": stranger}

        found = await violation(insert(connection, "task_collaborators", values), connection)

        assert found == "fk_task_collaborators_member_project_collaborators"

    async def test_leaving_the_project_removes_assignments(
        self, connection: AsyncConnection, project: str
    ) -> None:
        task = await insert_task(connection, project)
        member = await insert_user(connection)
        await add_member(connection, project, member)
        await insert(
            connection,
            "task_collaborators",
            {"task_id": task, "project_id": project, "user_id": member},
        )

        await update(
            connection,
            "DELETE FROM project_collaborators WHERE project_id = :p AND user_id = :u",
            p=project,
            u=member,
        )

        left = await connection.scalar(
            text("SELECT count(*) FROM task_collaborators WHERE user_id = :u"), {"u": member}
        )
        assert left == 0


class TestUniqueAmongActive:
    async def test_box_numbers(self, connection: AsyncConnection, project: str) -> None:
        await insert_box(connection, project, number=7, is_active=False)
        await insert_box(connection, project, number=7)
        await insert_box(connection, await insert_project(connection), number=7)

        found = await violation(insert_box(connection, project, number=7), connection)

        assert found == ACTIVE_BOX_NUMBER_UNIQUE_INDEX

    async def test_room_names_per_kind(self, connection: AsyncConnection, project: str) -> None:
        await insert_room(connection, project, name="Kitchen", is_active=False)
        await insert_room(connection, project, name="Kitchen")
        await insert_room(connection, project, name="Kitchen", kind="DESTINATION")

        found = await violation(insert_room(connection, project, name="Kitchen"), connection)

        assert found == ACTIVE_ROOM_NAME_UNIQUE_INDEX

    async def test_pending_task_reminders(self, connection: AsyncConnection, project: str) -> None:
        task = await insert_task(connection, project)
        user = await insert_user(connection)
        reminder = {
            "type": "TASK_REMINDER",
            "task_id": task,
            "user_id": user,
            "reminder_offset_hours": 24,
        }
        await insert_notification(connection, project, **reminder, status="SENT")
        await insert_notification(connection, project, **reminder)

        found = await violation(insert_notification(connection, project, **reminder), connection)

        assert found == "uq_notifications_task_reminder_pending"

    async def test_pending_moving_date_reminders(
        self, connection: AsyncConnection, project: str
    ) -> None:
        user = await insert_user(connection)
        reminder = {
            "type": "MOVING_DATE_REMINDER",
            "task_id": None,
            "user_id": user,
            "reminder_offset_days": 7,
        }
        await insert_notification(connection, project, **reminder, status="CANCELLED")
        await insert_notification(connection, project, **reminder)

        found = await violation(insert_notification(connection, project, **reminder), connection)

        assert found == "uq_notifications_moving_date_pending"
