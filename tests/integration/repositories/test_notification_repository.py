"""Bulk cancellation of the notification outbox: only pending rows change, filters hold."""

from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.common.ids import ProjectId, UserId
from app.domain.common.reminders import MovingReminderOffset
from app.domain.notifications.notification import (
    MovingDateReminder,
    Notification,
    NotificationTarget,
    TaskAssigned,
)
from app.domain.projects.entity import Project
from app.domain.tasks.entity import Task
from app.domain.tasks.value_objects import TaskTitle
from app.infrastructure.db.mappers.notification import NotificationMapper
from app.infrastructure.db.mappers.task import TaskMapper
from app.infrastructure.db.models import NotificationModel
from app.infrastructure.db.repositories.notification import SqlAlchemyNotificationRepository
from app.infrastructure.db.repositories.project import SqlAlchemyProjectRepository
from app.infrastructure.db.repositories.user import SqlAlchemyUserRepository
from tests.factories import NOW, make_project, make_user

type SessionMaker = async_sessionmaker[AsyncSession]


class Outbox:
    """Two projects with notifications of every kind that the cancel methods filter on."""

    def __init__(self) -> None:
        owner, member = make_user(username="owner"), make_user(username="member")
        self.owner, self.member = owner.id, member.id
        self.users = [owner, member]
        self.project = make_project(owner_id=owner.id)
        self.project.add_member(member.id)
        self.other_project = make_project(owner_id=owner.id)
        self.task = Task.create(project_id=self.project.id, title=TaskTitle("Pack"))
        self.notifications: dict[str, Notification] = {}

        for user_id, who in ((self.owner, "owner"), (self.member, "member")):
            for days in (1, 7):
                self._add(f"{who}/{days}", self.project, user_id, MovingDateReminder(_days(days)))
        self._add("member/task", self.project, self.member, TaskAssigned(self.task.id))
        sent = self._add("owner/3/sent", self.project, self.owner, MovingDateReminder(_days(3)))
        sent.mark_sent(now=NOW)
        self._add("other/1", self.other_project, self.owner, MovingDateReminder(_days(1)))

    def _add(
        self, name: str, project: Project, user_id: UserId, target: NotificationTarget
    ) -> Notification:
        notification = Notification.schedule(
            project_id=project.id,
            user_id=user_id,
            target=target,
            at=NOW + timedelta(days=1),
            now=NOW,
        )
        assert notification is not None
        self.notifications[name] = notification
        return notification

    async def save(self, session_maker: SessionMaker) -> None:
        async with session_maker() as session:
            for user in self.users:
                await SqlAlchemyUserRepository(session).add(user)
            for project in (self.project, self.other_project):
                await SqlAlchemyProjectRepository(session).add(project)
            session.add(TaskMapper.to_model(self.task))
            await session.flush()
            for notification in self.notifications.values():
                session.add(NotificationMapper.to_model(notification))
            await session.commit()

    async def statuses(self, session_maker: SessionMaker) -> dict[str, str]:
        names: dict[UUID, str] = {
            notification.id: name for name, notification in self.notifications.items()
        }
        async with session_maker() as session:
            rows = await session.execute(select(NotificationModel.id, NotificationModel.status))
            return {names[id_]: status for id_, status in rows}


def _days(days: int) -> MovingReminderOffset:
    return MovingReminderOffset(days)


@pytest.fixture
async def outbox(session_maker: SessionMaker) -> Outbox:
    outbox = Outbox()
    await outbox.save(session_maker)
    return outbox


async def cancel_pending(
    session_maker: SessionMaker, project_id: ProjectId, user_id: UserId | None = None
) -> int:
    async with session_maker() as session:
        cancelled = await SqlAlchemyNotificationRepository(session).cancel_pending(
            project_id, user_id=user_id
        )
        await session.commit()
        return cancelled


async def cancel_moving(
    session_maker: SessionMaker,
    project_id: ProjectId,
    offsets: set[MovingReminderOffset] | None = None,
) -> int:
    async with session_maker() as session:
        cancelled = await SqlAlchemyNotificationRepository(session).cancel_pending_moving_reminders(
            project_id, offsets=offsets
        )
        await session.commit()
        return cancelled


UNTOUCHED = {
    "owner/1": "PENDING",
    "owner/7": "PENDING",
    "member/1": "PENDING",
    "member/7": "PENDING",
    "member/task": "PENDING",
    "owner/3/sent": "SENT",
    "other/1": "PENDING",
}


class TestCancelPending:
    async def test_every_pending_notification_of_the_project(
        self, session_maker: SessionMaker, outbox: Outbox
    ) -> None:
        cancelled = await cancel_pending(session_maker, outbox.project.id)

        assert cancelled == 5
        assert await outbox.statuses(session_maker) == UNTOUCHED | {
            "owner/1": "CANCELLED",
            "owner/7": "CANCELLED",
            "member/1": "CANCELLED",
            "member/7": "CANCELLED",
            "member/task": "CANCELLED",
        }

    async def test_only_of_one_user(self, session_maker: SessionMaker, outbox: Outbox) -> None:
        cancelled = await cancel_pending(session_maker, outbox.project.id, outbox.member)

        assert cancelled == 3
        assert await outbox.statuses(session_maker) == UNTOUCHED | {
            "member/1": "CANCELLED",
            "member/7": "CANCELLED",
            "member/task": "CANCELLED",
        }

    async def test_is_idempotent(self, session_maker: SessionMaker, outbox: Outbox) -> None:
        await cancel_pending(session_maker, outbox.project.id)

        assert await cancel_pending(session_maker, outbox.project.id) == 0


class TestCancelPendingMovingReminders:
    async def test_all_offsets(self, session_maker: SessionMaker, outbox: Outbox) -> None:
        cancelled = await cancel_moving(session_maker, outbox.project.id)

        assert cancelled == 4
        assert await outbox.statuses(session_maker) == UNTOUCHED | {
            "owner/1": "CANCELLED",
            "owner/7": "CANCELLED",
            "member/1": "CANCELLED",
            "member/7": "CANCELLED",
        }

    async def test_given_offsets_only(self, session_maker: SessionMaker, outbox: Outbox) -> None:
        cancelled = await cancel_moving(session_maker, outbox.project.id, {_days(7), _days(3)})

        assert cancelled == 2  # offset 3 was already sent
        assert await outbox.statuses(session_maker) == UNTOUCHED | {
            "owner/7": "CANCELLED",
            "member/7": "CANCELLED",
        }

    async def test_no_offsets_cancel_nothing(
        self, session_maker: SessionMaker, outbox: Outbox
    ) -> None:
        cancelled = await cancel_moving(session_maker, outbox.project.id, set())

        assert cancelled == 0
        assert await outbox.statuses(session_maker) == UNTOUCHED
