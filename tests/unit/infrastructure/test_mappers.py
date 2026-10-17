"""Domain -> ORM -> domain keeps every attribute (no database: ORM objects are plain here)."""

import dataclasses
from datetime import timedelta
from typing import Any

import pytest

from app.domain.audit.entity import AuditAction, AuditEntry
from app.domain.common.ids import BoxId, ProjectId, TaskId, UserId, new_id
from app.domain.common.reminders import ReminderOffset
from app.domain.inventory.value_objects import Priority, Quantity
from app.domain.notifications.notification import (
    BoxContentsReminder,
    Notification,
    NotificationTarget,
    TaskAssigned,
    TaskReminder,
)
from app.domain.notifications.push_subscription import PushEndpoint, PushKeys, PushSubscription
from app.domain.projects.value_objects import Address, QrCodeStrategy
from app.domain.tasks.value_objects import TaskDescription
from app.infrastructure.db.mappers.audit import AuditEntryMapper
from app.infrastructure.db.mappers.inventory import BoxMapper, ItemMapper, RoomMapper
from app.infrastructure.db.mappers.notification import (
    InvalidNotificationRowError,
    NotificationMapper,
    PushSubscriptionMapper,
)
from app.infrastructure.db.mappers.project import ProjectMapper
from app.infrastructure.db.mappers.task import TaskMapper
from tests.factories import NOW, make_box, make_item, make_project, make_room, make_task


def state(entity: Any) -> dict[str, Any]:
    """All attributes (entities compare by id only)."""
    return {field.name: getattr(entity, field.name) for field in dataclasses.fields(entity)}


def test_project_with_members_and_addresses() -> None:
    project = make_project()
    project.add_member(UserId(new_id()))
    project.change_addresses(origin=Address("Москва"), destination=None)
    project.change_qr_code_strategy(QrCodeStrategy.OPEN)

    model = ProjectMapper.to_model(project)

    assert {row.user_id for row in model.collaborators} == project.member_ids
    assert state(ProjectMapper.to_domain(model)) == state(project)


def test_room() -> None:
    room = make_room()

    assert state(RoomMapper.to_domain(RoomMapper.to_model(room))) == state(room)


def test_box() -> None:
    box = make_box()

    model = BoxMapper.to_model(box)

    assert model.created_by_id == box.created_by
    assert state(BoxMapper.to_domain(model)) == state(box)


@pytest.mark.parametrize("packed", [False, True])
def test_item(packed: bool) -> None:
    item = make_item()
    item.change_quantity(Quantity(3))
    item.set_priority(Priority.FIRST_NECESSITY)
    if packed:
        item.put_into(make_box(project_id=item.project_id))

    model = ItemMapper.to_model(item)

    assert model.priority == 0  # stored as a number, sorts without CASE
    assert state(ItemMapper.to_domain(model)) == state(item)


def test_task_with_assignees_and_reminders() -> None:
    task = make_task(due_at=NOW + timedelta(days=3))
    task.change_description(TaskDescription("Call two companies"))
    task.assign(UserId(new_id()))
    task.add_reminder(ReminderOffset(24))
    task.add_reminder(ReminderOffset(2))
    task.complete(now=NOW)

    model = TaskMapper.to_model(task)

    assert model.is_done
    assert [row.offset_hours for row in model.reminders] == [2, 24]
    assert all(row.project_id == task.project_id for row in model.assignees)
    assert state(TaskMapper.to_domain(model)) == state(task)


@pytest.mark.parametrize(
    "target",
    [
        TaskAssigned(TaskId(new_id())),
        TaskReminder(TaskId(new_id()), ReminderOffset(48)),
        BoxContentsReminder(BoxId(new_id())),
    ],
)
def test_notification(target: NotificationTarget) -> None:
    notification = Notification.schedule(
        project_id=ProjectId(new_id()), user_id=UserId(new_id()), target=target, at=NOW, now=NOW
    )
    assert notification is not None
    notification.record_failure("timeout", now=NOW)

    model = NotificationMapper.to_model(notification)

    assert model.type == notification.type.value
    assert state(NotificationMapper.to_domain(model)) == state(notification)


def test_notification_row_that_breaks_the_type_rule_is_rejected() -> None:
    notification = Notification.schedule(
        project_id=ProjectId(new_id()),
        user_id=UserId(new_id()),
        target=TaskAssigned(TaskId(new_id())),
        at=NOW,
        now=NOW,
    )
    assert notification is not None
    model = NotificationMapper.to_model(notification)
    model.box_id = new_id()

    with pytest.raises(InvalidNotificationRowError):
        NotificationMapper.to_domain(model)


def test_push_subscription() -> None:
    subscription = PushSubscription.subscribe(
        user_id=UserId(new_id()),
        endpoint=PushEndpoint("https://push.example/abc"),
        keys=PushKeys(p256dh="BNcRdreALRFX", auth="tBHItJI5svbpez7KI4CCXg"),
    )

    restored = PushSubscriptionMapper.to_domain(PushSubscriptionMapper.to_model(subscription))

    assert state(restored) == state(subscription)


def test_audit_entry_stores_the_derived_entity_type() -> None:
    entry = AuditEntry.record(
        project_id=ProjectId(new_id()),
        actor_id=UserId(new_id()),
        action=AuditAction.ITEM_MARKED_LOST,
        entity_id=new_id(),
        now=NOW,
        details={"box": "7"},
    )

    model = AuditEntryMapper.to_model(entry)

    assert model.entity_type == "ITEM"
    assert state(AuditEntryMapper.to_domain(model)) == state(entry)
