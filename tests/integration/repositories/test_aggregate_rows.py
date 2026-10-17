"""Aggregates survive a trip through the database via their mappers.

Repositories will save with add()/merge() of the mapped model; children of an
aggregate (project members, task assignees and reminders) must follow: rows
removed from the aggregate are deleted, new ones inserted.
"""

import dataclasses
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.audit.entity import AuditAction, AuditEntry
from app.domain.common.ids import UserId
from app.domain.common.reminders import MovingReminderOffset, ReminderOffset
from app.domain.inventory.box import Box
from app.domain.inventory.item import Item
from app.domain.inventory.value_objects import BoxNumber, BoxTitle, ItemName, RoomKind
from app.domain.notifications.notification import BoxContentsReminder, Notification
from app.domain.notifications.push_subscription import PushEndpoint, PushKeys, PushSubscription
from app.domain.projects.entity import Project
from app.domain.tasks.entity import Task
from app.domain.tasks.value_objects import TaskPhase, TaskTitle
from app.infrastructure.db.base import Base
from app.infrastructure.db.mappers.audit import AuditEntryMapper
from app.infrastructure.db.mappers.inventory import BoxMapper, ItemMapper, RoomMapper
from app.infrastructure.db.mappers.notification import NotificationMapper, PushSubscriptionMapper
from app.infrastructure.db.mappers.project import ProjectMapper
from app.infrastructure.db.mappers.task import TaskMapper
from app.infrastructure.db.mappers.user import UserMapper
from app.infrastructure.db.models import (
    AuditLogModel,
    BoxModel,
    ItemModel,
    NotificationModel,
    ProjectModel,
    PushSubscriptionModel,
    RoomModel,
    TaskModel,
)
from tests.factories import NOW, make_project, make_room, make_user

type SessionMaker = async_sessionmaker[AsyncSession]


def state(entity: Any) -> dict[str, Any]:
    return {field.name: getattr(entity, field.name) for field in dataclasses.fields(entity)}


async def add(session_maker: SessionMaker, *models: Base) -> None:
    async with session_maker() as session:
        for model in models:
            session.add(model)
            await session.flush()  # in the given order: parents before children
        await session.commit()


async def merge(session_maker: SessionMaker, model: Base) -> None:
    async with session_maker() as session:
        await session.merge(model)
        await session.commit()


async def load[M: Base](session_maker: SessionMaker, model_class: type[M], id_: Any) -> M:
    """What is actually stored: read in a fresh session."""
    async with session_maker() as session:
        model = await session.get(model_class, id_)
        assert model is not None
        return model


async def project_with_users(session_maker: SessionMaker, members: int = 0) -> Project:
    owner = make_user(username="owner")
    others = [make_user(username=f"member_{n}") for n in range(members)]
    project = make_project(owner_id=owner.id)
    for user in others:
        project.add_member(user.id)
    await add(
        session_maker,
        *(UserMapper.to_model(user) for user in [owner, *others]),
        ProjectMapper.to_model(project),
    )
    return project


async def test_project_members_and_reminders_follow_the_aggregate(
    session_maker: SessionMaker,
) -> None:
    project = await project_with_users(session_maker, members=2)
    project.add_moving_reminder(MovingReminderOffset(7))
    await merge(session_maker, ProjectMapper.to_model(project))
    stored = ProjectMapper.to_domain(await load(session_maker, ProjectModel, project.id))
    assert state(stored) == state(project)

    leaving = sorted(project.member_ids - {project.owner_id})[0]
    project.remove_member(leaving)
    project.remove_moving_reminder(MovingReminderOffset(7))
    project.add_moving_reminder(MovingReminderOffset(1))
    await merge(session_maker, ProjectMapper.to_model(project))

    stored = ProjectMapper.to_domain(await load(session_maker, ProjectModel, project.id))
    assert state(stored) == state(project)
    assert leaving not in stored.member_ids


async def test_task_assignees_and_reminders_follow_the_aggregate(
    session_maker: SessionMaker,
) -> None:
    project = await project_with_users(session_maker, members=1)
    member = next(iter(project.member_ids - {project.owner_id}))
    task = Task.create(
        project_id=project.id,
        title=TaskTitle("Pack"),
        phase=TaskPhase.DURING,
        due_at=NOW + timedelta(days=5),
    )
    task.assign(member)
    task.add_reminder(ReminderOffset(24))
    await add(session_maker, TaskMapper.to_model(task))

    task.unassign(member)
    task.assign(project.owner_id)
    task.remove_reminder(ReminderOffset(24))
    task.add_reminder(ReminderOffset(2))
    task.complete(now=NOW)
    task.move_to_phase(TaskPhase.AFTER)
    await merge(session_maker, TaskMapper.to_model(task))

    stored = TaskMapper.to_domain(await load(session_maker, TaskModel, task.id))
    assert state(stored) == state(task)
    assert stored.assignee_ids == {UserId(project.owner_id)}


async def test_inventory_notifications_and_audit(session_maker: SessionMaker) -> None:
    project = await project_with_users(session_maker)
    room = make_room(project_id=project.id)
    destination = make_room(project_id=project.id, kind=RoomKind.DESTINATION, name="Bedroom")
    box = Box.create(
        project_id=project.id,
        number=BoxNumber(1),
        destination=destination,
        created_by=project.owner_id,
        now=NOW,
        title=BoxTitle("Dishes"),
        is_fragile=True,
    )
    item = Item.create(project_id=project.id, source=room, name=ItemName("Kettle"))
    item.put_into(box)
    notification = Notification.schedule(
        project_id=project.id,
        user_id=project.owner_id,
        target=BoxContentsReminder(box.id),
        at=NOW + timedelta(hours=1),
        now=NOW,
    )
    assert notification is not None
    entry = AuditEntry.record(
        project_id=project.id,
        actor_id=project.owner_id,
        action=AuditAction.BOX_CREATED,
        entity_id=box.id,
        now=NOW,
        details={"number": str(box.number)},
    )
    subscription = PushSubscription.subscribe(
        user_id=project.owner_id,
        endpoint=PushEndpoint("https://push.example/one"),
        keys=PushKeys(p256dh="BNcRdreALRFX", auth="tBHItJI5svbpez7KI4CCXg"),
    )

    await add(
        session_maker,
        RoomMapper.to_model(room),
        RoomMapper.to_model(destination),
        BoxMapper.to_model(box),
        ItemMapper.to_model(item),
        NotificationMapper.to_model(notification),
        AuditEntryMapper.to_model(entry),
        PushSubscriptionMapper.to_model(subscription),
    )

    assert state(RoomMapper.to_domain(await load(session_maker, RoomModel, room.id))) == state(room)
    assert state(BoxMapper.to_domain(await load(session_maker, BoxModel, box.id))) == state(box)
    assert state(ItemMapper.to_domain(await load(session_maker, ItemModel, item.id))) == state(item)
    stored_notification = await load(session_maker, NotificationModel, notification.id)
    assert state(NotificationMapper.to_domain(stored_notification)) == state(notification)
    stored_entry = await load(session_maker, AuditLogModel, entry.id)
    assert state(AuditEntryMapper.to_domain(stored_entry)) == state(entry)
    stored_subscription = await load(session_maker, PushSubscriptionModel, subscription.id)
    assert state(PushSubscriptionMapper.to_domain(stored_subscription)) == state(subscription)
