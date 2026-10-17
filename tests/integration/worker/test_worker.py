"""The notification worker end to end: the real container, PostgreSQL and WebPushSender.

Only the push service is fake (tests/integration/push_service.py); it decrypts
what it receives with the browser's keys, so the texts are checked as users see them.
"""

import asyncio
from collections import Counter
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from dishka import AsyncContainer
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from taskiq import InMemoryBroker

import app.worker.app
from app.config import DatabaseSettings, Settings, WorkerSettings
from app.ioc.container import create_container
from app.worker import DELIVER_TASK_NAME, WorkerApp, create_worker_app, drain
from tests.conftest import LogEvents
from tests.integration.migrations.rows import (
    add_member,
    insert_box,
    insert_notification,
    insert_project,
    insert_push_subscription,
    insert_task,
    insert_user,
)
from tests.integration.push_service import Browser, FakePushService, FakePushServiceProvider

type SessionMaker = async_sessionmaker[AsyncSession]


@dataclass(frozen=True, slots=True)
class Move:
    """A project and a member who gets its notifications."""

    project_id: str
    owner_id: str
    member_id: str


@pytest.fixture
async def move(engine: AsyncEngine, session_maker: SessionMaker) -> Move:
    # session_maker: the tables are emptied after the test.
    async with engine.begin() as connection:
        owner_id = await insert_user(connection)
        project_id = await insert_project(
            connection, owner_id=owner_id, name="Переезд в Казань", time_zone="Europe/Moscow"
        )
        member_id = await insert_user(connection)
        await add_member(connection, project_id, member_id)
    return Move(project_id, owner_id, member_id)


@pytest.fixture
def worker_factory(
    make_settings: Callable[..., Settings], push_session: aiohttp.ClientSession
) -> Callable[..., AsyncContainer]:
    """The worker's container; its sender reaches the fake push service."""

    def factory(**overrides: Any) -> AsyncContainer:
        return create_container(make_settings(**overrides), FakePushServiceProvider(push_session))

    return factory


@pytest.fixture
async def worker(worker_factory: Callable[..., AsyncContainer]) -> AsyncIterator[AsyncContainer]:
    container = worker_factory()
    yield container
    await container.close()


async def subscribe(engine: AsyncEngine, user_id: str, browser: Browser, **overrides: Any) -> str:
    async with engine.begin() as connection:
        return await insert_push_subscription(
            connection,
            user_id,
            endpoint=browser.endpoint,
            p256dh=browser.p256dh,
            auth=browser.auth,
            **overrides,
        )


async def notify(engine: AsyncEngine, move: Move, **overrides: Any) -> str:
    """A due TASK_ASSIGNED notification for the member, unless overridden."""
    async with engine.begin() as connection:
        if "task_id" not in overrides and "box_id" not in overrides:
            overrides["task_id"] = await insert_task(
                connection, move.project_id, title="Заказать грузчиков"
            )
        overrides.setdefault("scheduled_at", datetime.now(UTC) - timedelta(minutes=1))
        return await insert_notification(
            connection, move.project_id, user_id=move.member_id, **overrides
        )


async def execute(engine: AsyncEngine, statement: str, **params: Any) -> None:
    async with engine.begin() as connection:
        await connection.execute(text(statement), params)


async def notification(engine: AsyncEngine, notification_id: str) -> dict[str, Any]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT status, attempts, scheduled_at, sent_at, last_error"
                    " FROM notifications WHERE id = :id"
                ),
                {"id": notification_id},
            )
        ).one()
    return dict(row._mapping)  # the documented way to get a dict


async def subscription_is_active(engine: AsyncEngine, subscription_id: str) -> bool:
    async with engine.connect() as connection:
        active: bool = await connection.scalar(
            text("SELECT is_active FROM push_subscriptions WHERE id = :id"),
            {"id": subscription_id},
        )
    return active


def processed(log_events: LogEvents) -> list[dict[str, Any]]:
    return [e for e in log_events() if e["event"] == "notification_processed"]


class TestSent:
    async def test_task_assigned_reaches_the_browser_in_russian(
        self,
        worker: AsyncContainer,
        engine: AsyncEngine,
        move: Move,
        push_service: FakePushService,
        log_events: LogEvents,
    ) -> None:
        browser = push_service.browser()
        await subscribe(engine, move.member_id, browser)
        async with engine.begin() as connection:
            task_id = await insert_task(connection, move.project_id, title="Заказать грузчиков")
        notification_id = await notify(engine, move, task_id=task_id)
        before = datetime.now(UTC)

        count = await drain(worker, limit=10)

        assert count == 1
        assert push_service.payloads(browser) == [
            {
                "title": "Новая задача",
                "body": "Вам назначили «Заказать грузчиков» в проекте «Переезд в Казань»",
                "url": "/plan",
                "type": "TASK_ASSIGNED",
                "project_id": move.project_id,
                "notification_id": notification_id,
                "task_id": task_id,
            }
        ]
        row = await notification(engine, notification_id)
        assert (row["status"], row["attempts"], row["last_error"]) == ("SENT", 0, None)
        assert before <= row["sent_at"] <= datetime.now(UTC)
        [event] = processed(log_events)
        assert event["outcome"] == "SENT"
        assert event["notification_id"] == notification_id
        assert event["delay_seconds"] >= 60

    async def test_task_reminder_shows_the_due_time_in_the_project_zone(
        self, worker: AsyncContainer, engine: AsyncEngine, move: Move, push_service: FakePushService
    ) -> None:
        browser = push_service.browser()
        await subscribe(engine, move.member_id, browser)
        async with engine.begin() as connection:
            task_id = await insert_task(
                connection,
                move.project_id,
                title="Сдать ключи",
                due_at=datetime(2026, 11, 1, 9, 30, tzinfo=UTC),
            )
        await notify(engine, move, type="TASK_REMINDER", task_id=task_id, reminder_offset_hours=24)

        await drain(worker, limit=10)

        [payload] = push_service.payloads(browser)
        assert payload["title"] == "Скоро срок задачи"
        assert payload["body"] == "«Сдать ключи» — срок 01.11 в 12:30"  # Moscow is UTC+3
        assert (payload["type"], payload["task_id"]) == ("TASK_REMINDER", task_id)

    async def test_box_contents_reminder_links_to_the_box(
        self, worker: AsyncContainer, engine: AsyncEngine, move: Move, push_service: FakePushService
    ) -> None:
        browser = push_service.browser()
        await subscribe(engine, move.member_id, browser)
        async with engine.begin() as connection:
            box_id = await insert_box(connection, move.project_id, number=7)
            qr_token = await connection.scalar(
                text("SELECT qr_token FROM boxes WHERE id = :id"), {"id": box_id}
            )
        await notify(engine, move, type="BOX_CONTENTS_REMINDER", box_id=box_id)

        await drain(worker, limit=10)

        [payload] = push_service.payloads(browser)
        assert payload["title"] == "Опишите коробку № 7"
        assert payload["body"] == (
            "Проект «Переезд в Казань»: добавьте вещи, пока коробка не собрана"
        )
        assert payload["url"] == f"/b/{qr_token}"
        assert payload["box_id"] == box_id

    async def test_moving_date_reminder(
        self, worker: AsyncContainer, engine: AsyncEngine, move: Move, push_service: FakePushService
    ) -> None:
        browser = push_service.browser()
        await subscribe(engine, move.member_id, browser)
        for days in (0, 1, 3):
            await notify(
                engine, move, type="MOVING_DATE_REMINDER", task_id=None, reminder_offset_days=days
            )

        await drain(worker, limit=10)

        payloads = push_service.payloads(browser)
        assert sorted(p["title"] for p in payloads) == [
            "До переезда 3 дня",
            "Завтра переезд",
            "Сегодня переезд",
        ]
        assert {(p["body"], p["url"]) for p in payloads} == {
            ("Проект «Переезд в Казань»: проверьте план и коробки", "/dashboard")
        }

    async def test_every_active_browser_of_the_recipient_gets_it(
        self, worker: AsyncContainer, engine: AsyncEngine, move: Move, push_service: FakePushService
    ) -> None:
        phone, laptop, old = (push_service.browser() for _ in range(3))
        await subscribe(engine, move.member_id, phone)
        await subscribe(engine, move.member_id, laptop)
        await subscribe(engine, move.member_id, old, is_active=False)
        owners_browser = push_service.browser()
        await subscribe(engine, move.owner_id, owners_browser)
        notification_id = await notify(engine, move)

        await drain(worker, limit=10)

        assert len(push_service.received_by(phone)) == 1
        assert len(push_service.received_by(laptop)) == 1
        assert push_service.received_by(old) == []
        assert push_service.received_by(owners_browser) == [], "only the recipient"
        assert (await notification(engine, notification_id))["status"] == "SENT"

    async def test_one_failing_browser_does_not_block_the_others(
        self, worker: AsyncContainer, engine: AsyncEngine, move: Move, push_service: FakePushService
    ) -> None:
        broken, working = push_service.browser(), push_service.browser()
        push_service.answer(broken, 500)
        await subscribe(engine, move.member_id, broken)
        await subscribe(engine, move.member_id, working)
        notification_id = await notify(engine, move)

        await drain(worker, limit=10)

        row = await notification(engine, notification_id)
        assert (row["status"], row["attempts"]) == ("SENT", 0)
        assert len(push_service.payloads(working)) == 1


class TestSkipped:
    async def test_without_subscriptions(
        self,
        worker: AsyncContainer,
        engine: AsyncEngine,
        move: Move,
        push_service: FakePushService,
        log_events: LogEvents,
    ) -> None:
        notification_id = await notify(engine, move)

        count = await drain(worker, limit=10)

        assert count == 1
        row = await notification(engine, notification_id)
        assert (row["status"], row["sent_at"]) == ("SKIPPED", None)
        assert push_service.received == []
        assert processed(log_events)[0]["outcome"] == "SKIPPED"
        # A deactivated subscription is as good as none.
        await subscribe(engine, move.member_id, push_service.browser(), is_active=False)
        again = await notify(engine, move)
        await drain(worker, limit=10)
        assert (await notification(engine, again))["status"] == "SKIPPED"
        assert push_service.received == []


class TestGone:
    async def test_gone_subscription_is_deactivated(
        self,
        worker: AsyncContainer,
        engine: AsyncEngine,
        move: Move,
        push_service: FakePushService,
        log_events: LogEvents,
    ) -> None:
        working_id = await subscribe(engine, move.member_id, push_service.browser())
        gone_ids, statuses = [], []
        for status in (404, 410):
            gone = push_service.browser()
            push_service.answer(gone, status)
            gone_ids.append(await subscribe(engine, move.member_id, gone))
            notification_id = await notify(engine, move)
            await drain(worker, limit=10)
            statuses.append((await notification(engine, notification_id))["status"])

        assert statuses == ["SENT", "SENT"]
        assert [await subscription_is_active(engine, i) for i in gone_ids] == [False, False]
        assert await subscription_is_active(engine, working_id)
        events = [e for e in log_events() if e["event"] == "push_subscription_gone"]
        assert [(e["subscription_id"], e["user_id"]) for e in events] == [
            (gone_id, move.member_id) for gone_id in gone_ids
        ]

    async def test_all_gone_is_skipped_and_never_tried_again(
        self, worker: AsyncContainer, engine: AsyncEngine, move: Move, push_service: FakePushService
    ) -> None:
        gone = push_service.browser()
        push_service.answer(gone, 410)
        await subscribe(engine, move.member_id, gone)
        first = await notify(engine, move)
        await drain(worker, limit=10)
        second = await notify(engine, move)

        await drain(worker, limit=10)

        assert (await notification(engine, first))["status"] == "SKIPPED"
        assert (await notification(engine, second))["status"] == "SKIPPED"
        assert len(push_service.received_by(gone)) == 1, "the dead endpoint is not called again"


class TestCancelled:
    """The reason is checked again at sending time: it may have gone meanwhile."""

    async def test_project_deleted(
        self, worker: AsyncContainer, engine: AsyncEngine, move: Move, push_service: FakePushService
    ) -> None:
        await subscribe(engine, move.member_id, push_service.browser())
        notification_id = await notify(engine, move)
        await execute(
            engine, "UPDATE projects SET is_active = false WHERE id = :id", id=move.project_id
        )

        await drain(worker, limit=10)

        assert (await notification(engine, notification_id))["status"] == "CANCELLED"
        assert push_service.received == []

    async def test_recipient_left_the_project(
        self, worker: AsyncContainer, engine: AsyncEngine, move: Move, push_service: FakePushService
    ) -> None:
        await subscribe(engine, move.member_id, push_service.browser())
        notification_id = await notify(engine, move)
        await execute(
            engine,
            "DELETE FROM project_collaborators WHERE project_id = :project AND user_id = :user",
            project=move.project_id,
            user=move.member_id,
        )

        await drain(worker, limit=10)

        assert (await notification(engine, notification_id))["status"] == "CANCELLED"
        assert push_service.received == []

    async def test_recipient_deleted_the_account(
        self, worker: AsyncContainer, engine: AsyncEngine, move: Move, push_service: FakePushService
    ) -> None:
        await subscribe(engine, move.member_id, push_service.browser())
        notification_id = await notify(engine, move)
        await execute(
            engine, "UPDATE users SET is_active = false WHERE id = :id", id=move.member_id
        )

        await drain(worker, limit=10)

        assert (await notification(engine, notification_id))["status"] == "CANCELLED"
        assert push_service.received == []

    async def test_assigned_task_deleted(
        self, worker: AsyncContainer, engine: AsyncEngine, move: Move, push_service: FakePushService
    ) -> None:
        await subscribe(engine, move.member_id, push_service.browser())
        async with engine.begin() as connection:
            task_id = await insert_task(connection, move.project_id, is_active=False)
        notification_id = await notify(engine, move, task_id=task_id)

        await drain(worker, limit=10)

        assert (await notification(engine, notification_id))["status"] == "CANCELLED"

    async def test_reminded_task_no_longer_needs_it(
        self, worker: AsyncContainer, engine: AsyncEngine, move: Move, push_service: FakePushService
    ) -> None:
        await subscribe(engine, move.member_id, push_service.browser())
        changes: list[dict[str, Any]] = [
            {"is_done": True, "done_at": datetime(2026, 7, 1, tzinfo=UTC)},  # done
            {"is_active": False},  # deleted
            {"due_at": None},  # due time removed
        ]
        ids = []
        for change in changes:
            async with engine.begin() as connection:
                values = {"due_at": datetime(2026, 11, 1, tzinfo=UTC)} | change
                task_id = await insert_task(connection, move.project_id, **values)
            ids.append(
                await notify(
                    engine, move, type="TASK_REMINDER", task_id=task_id, reminder_offset_hours=24
                )
            )

        await drain(worker, limit=10)

        assert [(await notification(engine, i))["status"] for i in ids] == ["CANCELLED"] * 3
        assert push_service.received == []

    async def test_box_already_packed_or_deleted(
        self, worker: AsyncContainer, engine: AsyncEngine, move: Move, push_service: FakePushService
    ) -> None:
        await subscribe(engine, move.member_id, push_service.browser())
        boxes: list[dict[str, Any]] = [
            {"status": "PACKED"},
            {"status": "DELIVERED"},
            {"is_active": False},
        ]
        ids = []
        for box in boxes:
            async with engine.begin() as connection:
                box_id = await insert_box(connection, move.project_id, **box)
            ids.append(await notify(engine, move, type="BOX_CONTENTS_REMINDER", box_id=box_id))

        await drain(worker, limit=10)

        assert [(await notification(engine, i))["status"] for i in ids] == ["CANCELLED"] * 3
        assert push_service.received == []


class TestRetry:
    async def test_failure_is_retried_after_a_delay(
        self,
        worker: AsyncContainer,
        engine: AsyncEngine,
        move: Move,
        push_service: FakePushService,
        log_events: LogEvents,
    ) -> None:
        browser = push_service.browser()
        push_service.answer(browser, 503)
        await subscribe(engine, move.member_id, browser)
        notification_id = await notify(engine, move)
        before = datetime.now(UTC)

        first = await drain(worker, limit=10)
        again = await drain(worker, limit=10)

        assert (first, again) == (1, 0), "not due again before the delay"
        row = await notification(engine, notification_id)
        assert (row["status"], row["attempts"]) == ("PENDING", 1)
        assert before + timedelta(minutes=1) <= row["scheduled_at"]
        assert row["scheduled_at"] <= datetime.now(UTC) + timedelta(minutes=1)
        assert row["last_error"] == f"push.test:{push_service.port} answered 503"
        [event] = processed(log_events)
        assert (event["outcome"], event["attempts"], event["level"]) == ("RETRY", 1, "info")

    async def test_the_delay_doubles(
        self, worker: AsyncContainer, engine: AsyncEngine, move: Move, push_service: FakePushService
    ) -> None:
        browser = push_service.browser()
        push_service.answer(browser, 500)
        await subscribe(engine, move.member_id, browser)
        notification_id = await notify(engine, move, attempts=2)
        before = datetime.now(UTC)

        await drain(worker, limit=10)

        row = await notification(engine, notification_id)
        assert (row["status"], row["attempts"]) == ("PENDING", 3)
        assert before + timedelta(minutes=4) <= row["scheduled_at"]  # 1, 2, 4 minutes
        assert row["scheduled_at"] <= datetime.now(UTC) + timedelta(minutes=4)

    async def test_gives_up_after_the_last_attempt(
        self,
        worker: AsyncContainer,
        engine: AsyncEngine,
        move: Move,
        push_service: FakePushService,
        log_events: LogEvents,
    ) -> None:
        browser = push_service.browser()
        push_service.answer(browser, 500)
        await subscribe(engine, move.member_id, browser)
        notification_id = await notify(engine, move, attempts=4)

        await drain(worker, limit=10)

        row = await notification(engine, notification_id)
        assert (row["status"], row["attempts"]) == ("FAILED", 5)
        [event] = processed(log_events)
        assert (event["outcome"], event["level"]) == ("FAILED", "warning"), "given up: alert"

    async def test_unreachable_push_service(
        self, worker: AsyncContainer, engine: AsyncEngine, move: Move
    ) -> None:
        await subscribe(engine, move.member_id, Browser.create("https://push.test:1/push/x"))
        notification_id = await notify(engine, move)

        await drain(worker, limit=10)

        row = await notification(engine, notification_id)
        assert (row["status"], row["attempts"]) == ("PENDING", 1)
        assert row["last_error"].startswith("push.test:1: ")


class TestDrain:
    async def test_future_and_finished_notifications_are_left_alone(
        self, worker: AsyncContainer, engine: AsyncEngine, move: Move, push_service: FakePushService
    ) -> None:
        await subscribe(engine, move.member_id, push_service.browser())
        later = datetime.now(UTC) + timedelta(hours=1)
        notification_id = await notify(engine, move, scheduled_at=later)
        for status in ("SENT", "FAILED", "CANCELLED", "SKIPPED"):
            await notify(engine, move, status=status)

        count = await drain(worker, limit=10)

        assert count == 0
        row = await notification(engine, notification_id)
        assert (row["status"], row["attempts"], row["scheduled_at"]) == ("PENDING", 0, later)
        assert push_service.received == []

    async def test_stops_at_the_limit(
        self, worker: AsyncContainer, engine: AsyncEngine, move: Move, push_service: FakePushService
    ) -> None:
        browser = push_service.browser()
        await subscribe(engine, move.member_id, browser)
        ids = [
            await notify(engine, move, scheduled_at=datetime.now(UTC) - timedelta(minutes=m))
            for m in (3, 2, 1)
        ]

        first = await drain(worker, limit=2)
        statuses = [(await notification(engine, i))["status"] for i in ids]
        rest = await drain(worker, limit=2)

        assert (first, rest) == (2, 1)
        assert statuses == ["SENT", "SENT", "PENDING"], "the oldest first"
        sent = [p["notification_id"] for p in push_service.payloads(browser)]
        assert sent == ids

    async def test_a_stop_request_is_honoured_between_notifications(
        self, worker: AsyncContainer, engine: AsyncEngine, move: Move, push_service: FakePushService
    ) -> None:
        browser = push_service.browser()
        await subscribe(engine, move.member_id, browser)
        ids = [
            await notify(engine, move, scheduled_at=datetime.now(UTC) - timedelta(minutes=m))
            for m in (2, 1)
        ]
        stop = asyncio.Event()

        async def stop_when_the_first_is_on_its_way() -> None:
            await push_service.wait_for(1)
            stop.set()

        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(stop_when_the_first_is_on_its_way())
            count = await drain(worker, limit=10, stop=stop)

        assert count == 1
        statuses = [(await notification(engine, i))["status"] for i in ids]
        assert statuses == ["SENT", "PENDING"], "the started one is finished, not abandoned"

    async def test_nothing_starts_once_stopped(
        self, worker: AsyncContainer, engine: AsyncEngine, move: Move
    ) -> None:
        notification_id = await notify(engine, move)
        stop = asyncio.Event()
        stop.set()

        assert await drain(worker, limit=10, stop=stop) == 0
        assert (await notification(engine, notification_id))["status"] == "PENDING"

    async def test_parallel_workers_never_send_a_notification_twice(
        self,
        worker_factory: Callable[..., AsyncContainer],
        engine: AsyncEngine,
        move: Move,
        push_service: FakePushService,
    ) -> None:
        browser = push_service.browser()
        await subscribe(engine, move.member_id, browser)
        ids = {await notify(engine, move) for _ in range(6)}
        # Each message waits for a second one: the workers must send side by side.
        push_service.answer_in_pairs()
        first, second = worker_factory(), worker_factory()
        try:
            counts = await asyncio.gather(drain(first, limit=10), drain(second, limit=10))
        finally:
            await first.close()
            await second.close()

        sent = Counter(p["notification_id"] for p in push_service.payloads(browser))
        assert sum(counts) == 6
        assert all(count > 0 for count in counts), "both workers took part"
        assert sent == dict.fromkeys(ids, 1)
        statuses = {(await notification(engine, i))["status"] for i in ids}
        assert statuses == {"SENT"}


class TestTaskiqApp:
    """The periodic task as taskiq runs it: through a broker (in memory here, Redis in
    production), with the worker's startup and shutdown hooks."""

    @pytest.fixture
    def worker_settings(
        self, make_settings: Callable[..., Settings], tmp_path: Path
    ) -> Callable[..., Settings]:
        def factory(**overrides: Any) -> Settings:
            worker = WorkerSettings(heartbeat_file=tmp_path / "state" / "heartbeat")
            return make_settings(worker=worker, **overrides)

        return factory

    @pytest.fixture
    def with_fake_push_service(
        self, monkeypatch: pytest.MonkeyPatch, push_session: aiohttp.ClientSession
    ) -> None:
        def create(settings: Settings) -> AsyncContainer:
            return create_container(settings, FakePushServiceProvider(push_session))

        monkeypatch.setattr(app.worker.app, "create_container", create)

    @staticmethod
    async def run_once(worker_app: WorkerApp) -> int:
        task = await worker_app.deliver.kiq()
        result = await task.wait_result(timeout=10)
        assert not result.is_err, result.error
        processed: int = result.return_value
        return processed

    def test_the_task_is_scheduled_by_interval_under_a_fixed_id(
        self, worker_settings: Callable[..., Settings]
    ) -> None:
        settings = worker_settings()
        worker_app = create_worker_app(settings, broker=InMemoryBroker())

        assert worker_app.deliver.task_name == DELIVER_TASK_NAME
        assert worker_app.deliver.labels["schedule"] == [
            {"interval": settings.worker.interval_seconds, "schedule_id": DELIVER_TASK_NAME}
        ]

    async def test_the_scheduler_sees_one_schedule(
        self, worker_settings: Callable[..., Settings]
    ) -> None:
        worker_app = create_worker_app(worker_settings(), broker=InMemoryBroker())
        [source] = worker_app.scheduler.sources

        await source.startup()
        [schedule] = await source.get_schedules()

        assert (schedule.task_name, schedule.schedule_id, schedule.interval) == (
            DELIVER_TASK_NAME,
            DELIVER_TASK_NAME,
            15,
        )

    @pytest.mark.usefixtures("with_fake_push_service")
    async def test_a_run_sends_touches_the_heartbeat_and_logs(
        self,
        worker_settings: Callable[..., Settings],
        engine: AsyncEngine,
        move: Move,
        push_service: FakePushService,
        log_events: LogEvents,
    ) -> None:
        browser = push_service.browser()
        await subscribe(engine, move.member_id, browser)
        notification_id = await notify(engine, move)
        settings = worker_settings()
        worker_app = create_worker_app(settings, broker=InMemoryBroker())

        await worker_app.broker.startup()
        assert settings.worker.heartbeat_file.exists(), "healthy right after the start"
        processed = await self.run_once(worker_app)
        idle = await self.run_once(worker_app)
        await worker_app.broker.shutdown()

        assert (processed, idle) == (1, 0)
        assert (await notification(engine, notification_id))["status"] == "SENT"
        assert len(push_service.payloads(browser)) == 1
        events = [e for e in log_events() if e["event"].startswith(("worker_", "notifications_"))]
        assert [e["event"] for e in events] == [
            "worker_started",
            "notifications_drained",
            "worker_stopped",
        ]
        assert (events[0]["version"], events[0]["interval"]) == ("test-version", 15)
        assert events[1]["count"] == 1

    async def test_a_failed_run_is_logged_and_leaves_the_heartbeat_stale(
        self,
        worker_settings: Callable[..., Settings],
        log_events: LogEvents,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        dead = DatabaseSettings(url=SecretStr("postgresql+asyncpg://app:app@127.0.0.1:1/none_test"))
        settings = worker_settings(database=dead)
        worker_app = create_worker_app(settings, broker=InMemoryBroker())
        await worker_app.broker.startup()
        settings.worker.heartbeat_file.unlink()

        processed = [await self.run_once(worker_app) for _ in range(2)]
        await worker_app.broker.shutdown()

        assert processed == [0, 0], "the task does not fail: the next tick simply tries again"
        failures = [e for e in log_events() if e["event"] == "worker_iteration_failed"]
        assert len(failures) == 2
        [record, *_] = [r for r in caplog.records if r.getMessage() == "worker_iteration_failed"]
        assert record.exc_info is not None, "with the traceback"
        assert not settings.worker.heartbeat_file.exists(), "a stale heartbeat flags the worker"
