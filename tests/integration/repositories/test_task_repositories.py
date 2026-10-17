"""Tasks in PostgreSQL: the repository, the read model of the plan and the
cancellation of task notifications."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.exceptions import AssigneeNotCollaboratorError
from app.domain.common.ids import UserId
from app.domain.common.reminders import ReminderOffset
from app.domain.notifications.notification import (
    Notification,
    NotificationTarget,
    TaskAssigned,
    TaskReminder,
)
from app.domain.projects.entity import Project
from app.domain.tasks.entity import Task
from app.domain.tasks.value_objects import TaskPhase, TaskTitle
from app.domain.users.entity import User
from app.infrastructure.db.models import NotificationModel, UserModel
from app.infrastructure.db.queries.tasks import SqlAlchemyTaskReadModel
from app.infrastructure.db.repositories.notification import SqlAlchemyNotificationRepository
from app.infrastructure.db.repositories.project import SqlAlchemyProjectRepository
from app.infrastructure.db.repositories.task import SqlAlchemyTaskRepository
from app.infrastructure.db.repositories.user import SqlAlchemyUserRepository
from tests.factories import NOW, make_project, make_user

type SessionMaker = async_sessionmaker[AsyncSession]


@dataclass(frozen=True, slots=True)
class Plan:
    owner: User
    member: User
    stranger: User
    project: Project


@pytest.fixture
async def plan(session_maker: SessionMaker) -> Plan:
    owner, member, stranger = (make_user(username=name) for name in ("owner", "member", "other"))
    project = make_project(owner_id=owner.id)
    project.add_member(member.id)
    async with session_maker() as session:
        for user in (owner, member, stranger):
            await SqlAlchemyUserRepository(session).add(user)
        await SqlAlchemyProjectRepository(session).add(project)
        await session.commit()
    return Plan(owner, member, stranger, project)


def new_task(
    plan: Plan,
    title: str = "Pack",
    *,
    phase: TaskPhase = TaskPhase.BEFORE,
    due_at: datetime | None = None,
) -> Task:
    return Task.create(
        project_id=plan.project.id, title=TaskTitle(title), phase=phase, due_at=due_at
    )


async def save(session_maker: SessionMaker, *tasks: Task) -> None:
    async with session_maker() as session:
        for task in tasks:
            await SqlAlchemyTaskRepository(session).add(task)
        await session.commit()


class TestRepository:
    async def test_saves_and_changes_the_whole_aggregate(
        self, session_maker: SessionMaker, plan: Plan
    ) -> None:
        task = new_task(plan, due_at=NOW + timedelta(days=3))
        task.assign(plan.member.id)
        task.add_reminder(ReminderOffset(24))
        await save(session_maker, task)

        async with session_maker() as session:
            repository = SqlAlchemyTaskRepository(session)
            stored = await repository.get_by_id(task.id, for_update=True)
            assert stored is not None
            stored.unassign(plan.member.id)
            stored.assign(plan.owner.id)
            stored.reschedule(None)
            stored.complete(now=NOW)
            await repository.update(stored)
            await session.commit()

        async with session_maker() as session:
            reloaded = await SqlAlchemyTaskRepository(session).get_by_id(task.id)
        assert reloaded is not None
        assert (reloaded.assignee_ids, reloaded.reminders, reloaded.done_at) == (
            {plan.owner.id},
            set(),
            NOW,
        )

    async def test_an_assignee_outside_the_project_is_refused_by_the_database(
        self, session_maker: SessionMaker, plan: Plan
    ) -> None:
        """The use case checks membership first; this is the race with a member leaving."""
        stranger_assigned = new_task(plan)
        stranger_assigned.assign(plan.stranger.id)
        fine = new_task(plan, "Fine")

        async with session_maker() as session:
            repository = SqlAlchemyTaskRepository(session)
            with pytest.raises(AssigneeNotCollaboratorError):
                await repository.add(stranger_assigned)
            await repository.add(fine)  # a SAVEPOINT kept the transaction usable
            await session.commit()

        async with session_maker() as session:
            repository = SqlAlchemyTaskRepository(session)
            assert await repository.get_by_id(stranger_assigned.id) is None
            assert await repository.get_by_id(fine.id) is not None

    async def test_deleted_tasks_are_hidden(self, session_maker: SessionMaker, plan: Plan) -> None:
        task = new_task(plan)
        await save(session_maker, task)
        task.soft_delete()
        async with session_maker() as session:
            await SqlAlchemyTaskRepository(session).update(task)
            await session.commit()

        async with session_maker() as session:
            assert await SqlAlchemyTaskRepository(session).get_by_id(task.id) is None
            assert await SqlAlchemyTaskReadModel(session).get_many([task.id]) == []


async def test_read_model_lists_the_plan_in_order(session_maker: SessionMaker, plan: Plan) -> None:
    after = new_task(plan, "After", phase=TaskPhase.AFTER)
    undated = new_task(plan, "Undated")
    later = new_task(plan, "Later", due_at=NOW + timedelta(days=2))
    sooner = new_task(plan, "Sooner", due_at=NOW + timedelta(days=1))
    sooner.complete(now=NOW)
    for user in (plan.owner, plan.member):
        later.assign(user.id)
    later.add_reminder(ReminderOffset(24))
    later.add_reminder(ReminderOffset(2))
    await save(session_maker, after, undated, later, sooner)
    # A deleted account is hidden from the assignees, like from the collaborators.
    async with session_maker() as session:
        await session.execute(
            update(UserModel).where(UserModel.id == plan.owner.id).values(is_active=False)
        )
        await session.commit()

    async with session_maker() as session:
        read_model = SqlAlchemyTaskReadModel(session)

        async def titles(phase: TaskPhase | None = None, is_done: bool | None = None) -> list[str]:
            tasks = await read_model.project_tasks(plan.project.id, phase=phase, is_done=is_done)
            return [task.title for task in tasks]

        assert await titles() == ["Sooner", "Later", "Undated", "After"]
        assert await titles(phase=TaskPhase.AFTER) == ["After"]
        assert await titles(is_done=False) == ["Later", "Undated", "After"]
        [view] = await read_model.get_many([later.id])
        many = await read_model.get_many([after.id, sooner.id])

    assert [user.username for user in view.assignees] == ["member"]
    assert view.reminder_offsets_hours == (2, 24)
    assert view.created_at <= view.updated_at
    assert [task.title for task in many] == ["After", "Sooner"], "in the given order"


async def test_task_notifications_are_cancelled_by_task_user_and_offset(
    session_maker: SessionMaker, plan: Plan
) -> None:
    task, other = new_task(plan, due_at=NOW + timedelta(days=5)), new_task(plan, "Other")
    await save(session_maker, task, other)
    queued: dict[str, tuple[UserId, NotificationTarget]] = {
        "owner/24": (plan.owner.id, TaskReminder(task.id, ReminderOffset(24))),
        "owner/2": (plan.owner.id, TaskReminder(task.id, ReminderOffset(2))),
        "member/24": (plan.member.id, TaskReminder(task.id, ReminderOffset(24))),
        "member/assigned": (plan.member.id, TaskAssigned(task.id)),
        "other/assigned": (plan.member.id, TaskAssigned(other.id)),
    }
    ids = {}
    async with session_maker() as session:
        repository = SqlAlchemyNotificationRepository(session)
        for name, (user_id, target) in queued.items():
            notification = Notification.schedule(
                project_id=plan.project.id,
                user_id=user_id,
                target=target,
                at=NOW + timedelta(days=1),
                now=NOW,
            )
            assert notification is not None
            ids[notification.id] = name
            await repository.add(notification)
        await session.commit()

    async def cancelled_after(cancel: str) -> tuple[int, set[str]]:
        async with session_maker() as session:
            repository = SqlAlchemyNotificationRepository(session)
            if cancel == "reminders 24":
                count = await repository.cancel_pending_task_reminders(
                    task.id, offsets=[ReminderOffset(24)]
                )
            elif cancel == "of the member":
                count = await repository.cancel_pending_for_task(task.id, user_id=plan.member.id)
            else:
                count = await repository.cancel_pending_for_task(task.id)
            await session.commit()
            rows = await session.execute(
                select(NotificationModel.id).where(NotificationModel.status == "CANCELLED")
            )
            return count, {ids[row.id] for row in rows}

    assert await cancelled_after("reminders 24") == (2, {"owner/24", "member/24"})
    assert await cancelled_after("of the member") == (
        1,
        {"owner/24", "member/24", "member/assigned"},
    )
    assert await cancelled_after("everything") == (
        1,
        {"owner/24", "member/24", "member/assigned", "owner/2"},
    )
