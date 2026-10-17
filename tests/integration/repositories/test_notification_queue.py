"""The notifications table as the worker's queue: claiming, locking, saving results;
the facts the worker re-checks before sending; the subscription upsert."""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.application.interfaces.notification_context import (
    BoxFacts,
    NotificationContext,
    TaskFacts,
)
from app.domain.common.ids import BoxId, ProjectId, TaskId, UserId
from app.domain.common.reminders import MovingReminderOffset, ReminderOffset
from app.domain.notifications.notification import (
    BoxContentsReminder,
    MovingDateReminder,
    Notification,
    NotificationTarget,
    TaskAssigned,
    TaskReminder,
)
from app.domain.notifications.push_subscription import PushEndpoint, PushKeys, PushSubscription
from app.infrastructure.db.repositories.notification import SqlAlchemyNotificationRepository
from app.infrastructure.db.repositories.notification_context import (
    SqlAlchemyNotificationContextReader,
)
from app.infrastructure.db.repositories.push_subscription import (
    SqlAlchemyPushSubscriptionRepository,
)
from tests.integration.migrations.rows import (
    NOW,
    add_member,
    insert_box,
    insert_notification,
    insert_project,
    insert_task,
    insert_user,
)

type SessionMaker = async_sessionmaker[AsyncSession]

pytestmark = pytest.mark.usefixtures("session_maker")  # empties the tables afterwards


async def seed(engine: AsyncEngine, **overrides: Any) -> str:
    """A pending notification in a fresh project."""
    async with engine.begin() as connection:
        project_id = await insert_project(connection)
        return await insert_notification(connection, project_id, **overrides)


def at(minutes: int) -> datetime:
    return NOW + timedelta(minutes=minutes)


class TestClaimNextDue:
    async def test_the_oldest_due_one_first(
        self, engine: AsyncEngine, session: AsyncSession
    ) -> None:
        later = await seed(engine, scheduled_at=at(-1))
        oldest = await seed(engine, scheduled_at=at(-10))
        await seed(engine, scheduled_at=at(1))  # not due yet
        await seed(engine, scheduled_at=at(-60), status="SENT")
        await seed(engine, scheduled_at=at(-60), status="CANCELLED")

        claimed = await SqlAlchemyNotificationRepository(session).claim_next_due(NOW)

        assert claimed is not None
        assert str(claimed.id) == oldest
        assert str(claimed.id) != later

    async def test_due_exactly_now_counts(self, engine: AsyncEngine, session: AsyncSession) -> None:
        due = await seed(engine, scheduled_at=NOW)

        claimed = await SqlAlchemyNotificationRepository(session).claim_next_due(NOW)

        assert claimed is not None
        assert str(claimed.id) == due

    async def test_ties_are_broken_by_id(self, engine: AsyncEngine, session: AsyncSession) -> None:
        ids = sorted([await seed(engine, scheduled_at=NOW) for _ in range(3)], key=UUID)

        claimed = await SqlAlchemyNotificationRepository(session).claim_next_due(NOW)

        assert claimed is not None
        assert str(claimed.id) == ids[0]

    async def test_nothing_due(self, engine: AsyncEngine, session: AsyncSession) -> None:
        await seed(engine, scheduled_at=at(1))

        assert await SqlAlchemyNotificationRepository(session).claim_next_due(NOW) is None

    async def test_a_claimed_row_is_skipped_by_other_workers(
        self, engine: AsyncEngine, session_maker: SessionMaker
    ) -> None:
        first, second = (
            await seed(engine, scheduled_at=at(-2)),
            await seed(engine, scheduled_at=at(-1)),
        )

        async with session_maker() as worker_a, session_maker() as worker_b:
            claimed_a = await SqlAlchemyNotificationRepository(worker_a).claim_next_due(NOW)
            claimed_b = await SqlAlchemyNotificationRepository(worker_b).claim_next_due(NOW)
            async with session_maker() as worker_c:
                claimed_c = await SqlAlchemyNotificationRepository(worker_c).claim_next_due(NOW)

        assert claimed_a is not None
        assert claimed_b is not None
        assert (str(claimed_a.id), str(claimed_b.id)) == (first, second)
        assert claimed_c is None, "both rows are locked: skipped, not waited for"

    async def test_a_crashed_worker_loses_nothing(
        self, engine: AsyncEngine, session_maker: SessionMaker
    ) -> None:
        due = await seed(engine)
        async with session_maker() as crashed:
            assert await SqlAlchemyNotificationRepository(crashed).claim_next_due(NOW)
            await crashed.rollback()

        async with session_maker() as next_worker:
            claimed = await SqlAlchemyNotificationRepository(next_worker).claim_next_due(NOW)

        assert claimed is not None
        assert str(claimed.id) == due

    async def test_the_result_is_saved(
        self, engine: AsyncEngine, session_maker: SessionMaker
    ) -> None:
        due = await seed(engine, attempts=1)
        async with session_maker() as session:
            repository = SqlAlchemyNotificationRepository(session)
            claimed = await repository.claim_next_due(NOW)
            assert claimed is not None
            claimed.record_failure("push.test answered 500", now=NOW)
            await repository.update(claimed)
            await session.commit()

        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT status, attempts, scheduled_at, last_error"
                        " FROM notifications WHERE id = :id"
                    ),
                    {"id": due},
                )
            ).one()
        assert tuple(row) == ("PENDING", 2, NOW + timedelta(minutes=2), "push.test answered 500")


class TestNotificationContextReader:
    @pytest.fixture
    async def scene(self, engine: AsyncEngine) -> dict[str, Any]:
        async with engine.begin() as connection:
            owner = await insert_user(connection)
            project = await insert_project(
                connection, owner_id=owner, name="Переезд", time_zone="Asia/Yekaterinburg"
            )
            member = await insert_user(connection)
            await add_member(connection, project, member)
            outsider = await insert_user(connection)  # a member of another project only
            await insert_project(connection, owner_id=outsider)
            task = await insert_task(connection, project, title="Упаковать посуду", due_at=at(60))
            box = await insert_box(connection, project, number=12, status="PACKED")
            qr_token = await connection.scalar(
                text("SELECT qr_token FROM boxes WHERE id = :id"), {"id": box}
            )
        return {
            "project": project,
            "member": member,
            "outsider": outsider,
            "task": task,
            "box": box,
            "qr_token": qr_token,
        }

    @staticmethod
    def about(
        scene: dict[str, Any], target: NotificationTarget, user: str = "member"
    ) -> Notification:
        notification = Notification.schedule(
            project_id=ProjectId(UUID(scene["project"])),
            user_id=UserId(UUID(scene[user])),
            target=target,
            at=NOW,
            now=NOW,
        )
        assert notification is not None
        return notification

    async def test_task_notifications_see_the_task(
        self, session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        task_id = TaskId(UUID(scene["task"]))
        reader = SqlAlchemyNotificationContextReader(session)

        assigned = await reader.get(self.about(scene, TaskAssigned(task_id)))
        reminder = await reader.get(self.about(scene, TaskReminder(task_id, ReminderOffset(24))))

        expected = NotificationContext(
            project_name="Переезд",
            project_time_zone="Asia/Yekaterinburg",
            project_is_active=True,
            recipient_is_active=True,
            recipient_is_member=True,
            task=TaskFacts(title="Упаковать посуду", due_at=at(60), is_done=False, is_active=True),
            box=None,
        )
        assert assigned == reminder == expected

    async def test_box_notifications_see_the_box(
        self, session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        target = BoxContentsReminder(BoxId(UUID(scene["box"])))

        context = await SqlAlchemyNotificationContextReader(session).get(self.about(scene, target))

        assert context.task is None
        assert context.box == BoxFacts(
            number=12, status="PACKED", qr_token=scene["qr_token"], is_active=True
        )

    async def test_moving_date_reminders_see_the_project_only(
        self, session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        target = MovingDateReminder(MovingReminderOffset(1))

        context = await SqlAlchemyNotificationContextReader(session).get(self.about(scene, target))

        assert (context.project_name, context.task, context.box) == ("Переезд", None, None)

    async def test_current_state_of_the_task(
        self, engine: AsyncEngine, session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE tasks SET is_done = true, done_at = :now, is_active = false"
                    " WHERE id = :id"
                ),
                {"now": NOW, "id": scene["task"]},
            )
        target = TaskAssigned(TaskId(UUID(scene["task"])))

        context = await SqlAlchemyNotificationContextReader(session).get(self.about(scene, target))

        assert context.task is not None
        assert (context.task.is_done, context.task.is_active) == (True, False)

    async def test_a_deleted_project_and_recipient(
        self, engine: AsyncEngine, session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE projects SET is_active = false WHERE id = :id"),
                {"id": scene["project"]},
            )
            await connection.execute(
                text("UPDATE users SET is_active = false WHERE id = :id"), {"id": scene["member"]}
            )
        target = MovingDateReminder(MovingReminderOffset(1))

        context = await SqlAlchemyNotificationContextReader(session).get(self.about(scene, target))

        assert (context.project_is_active, context.recipient_is_active) == (False, False)
        assert context.recipient_is_member, "membership rows stay; activity decides"

    async def test_membership_is_of_this_project(
        self, session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        target = MovingDateReminder(MovingReminderOffset(1))

        context = await SqlAlchemyNotificationContextReader(session).get(
            self.about(scene, target, user="outsider")
        )

        assert context.recipient_is_active
        assert not context.recipient_is_member


class TestPushSubscriptionUpsert:
    async def test_add_of_a_known_endpoint_takes_the_row_over(
        self, engine: AsyncEngine, session_maker: SessionMaker
    ) -> None:
        async with engine.begin() as connection:
            first_user = UserId(UUID(await insert_user(connection)))
            second_user = UserId(UUID(await insert_user(connection)))
        endpoint = PushEndpoint("https://push.example.com/browser-1")
        original = PushSubscription.subscribe(
            user_id=first_user, endpoint=endpoint, keys=PushKeys(p256dh="old", auth="old")
        )
        async with session_maker() as session:
            await SqlAlchemyPushSubscriptionRepository(session).add(original)
            await session.commit()
        async with session_maker() as session:  # it was deactivated meanwhile
            stored = await SqlAlchemyPushSubscriptionRepository(session).get_by_endpoint(endpoint)
            assert stored is not None
            stored.deactivate()
            await SqlAlchemyPushSubscriptionRepository(session).update(stored)
            await session.commit()

        async with session_maker() as session:
            repository = SqlAlchemyPushSubscriptionRepository(session)
            # A concurrent subscribe: get_by_endpoint saw nothing, so it adds.
            racing = PushSubscription.subscribe(
                user_id=second_user, endpoint=endpoint, keys=PushKeys(p256dh="new", auth="new")
            )
            result = await repository.add(racing)
            await session.commit()
            reread = await repository.get_by_endpoint(endpoint)
            left_behind = await repository.list_active_for_user(first_user)

        assert result.id == original.id, "one row per browser"
        assert (result.user_id, result.keys, result.is_active) == (
            second_user,
            PushKeys(p256dh="new", auth="new"),
            True,
        )
        assert reread is not None
        assert (reread.user_id, reread.is_active) == (second_user, True)
        assert left_behind == []

    async def test_active_subscriptions_of_a_user_in_creation_order(
        self, engine: AsyncEngine, session_maker: SessionMaker
    ) -> None:
        async with engine.begin() as connection:
            user = UserId(UUID(await insert_user(connection)))
        keys = PushKeys(p256dh="k", auth="a")
        subscriptions = [
            PushSubscription.subscribe(
                user_id=user, endpoint=PushEndpoint(f"https://push.example.com/{n}"), keys=keys
            )
            for n in range(3)
        ]
        subscriptions[1].deactivate()
        async with session_maker() as session:
            for subscription in subscriptions:
                await SqlAlchemyPushSubscriptionRepository(session).add(subscription)
            await session.commit()

        async with session_maker() as session:
            active = await SqlAlchemyPushSubscriptionRepository(session).list_active_for_user(user)

        assert [s.endpoint.value for s in active] == [
            "https://push.example.com/0",
            "https://push.example.com/2",
        ]
