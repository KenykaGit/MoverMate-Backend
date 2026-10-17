"""Projects and their members end to end: HTTP contract plus what lands in the database.

Moving dates are relative to today, so queued reminders are always in the future
unless a test means otherwise.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.models import (
    AuditLogModel,
    NotificationModel,
    ProjectModel,
    RoomModel,
)
from tests.conftest import LogEvents
from tests.integration.helpers import (
    API,
    Session,
    add_collaborator,
    create_project,
    in_days,
    signed_up,
)

type SessionMaker = async_sessionmaker[AsyncSession]
type Reminder = tuple[str, int | None, datetime, str]
"""A moving date reminder as stored: (user_id, offset_days, scheduled_at, status)."""

MOVING_DATE = in_days(60)


def utc(day: date, hour: int) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=UTC)


def error_code(response: Response) -> str:
    code: str = response.json()["error"]["code"]
    return code


def project_url(project_id: str, suffix: str = "") -> str:
    return f"{API}/projects/{project_id}{suffix}"


async def moving_reminders(session_maker: SessionMaker, project_id: str) -> list[Reminder]:
    """Every moving date reminder of the project, in a stable order."""
    n = NotificationModel
    async with session_maker() as session:
        rows = await session.execute(
            select(n.user_id, n.reminder_offset_days, n.scheduled_at, n.status).where(
                n.project_id == UUID(project_id), n.type == "MOVING_DATE_REMINDER"
            )
        )
        return sorted(
            (str(user_id), offset, scheduled_at, status)
            for user_id, offset, scheduled_at, status in rows
        )


def with_status(reminders: list[Reminder], status: str) -> list[Reminder]:
    return [reminder for reminder in reminders if reminder[3] == status]


async def notification_ids(session_maker: SessionMaker, project_id: str) -> set[tuple[UUID, str]]:
    async with session_maker() as session:
        rows = await session.execute(
            select(NotificationModel.id, NotificationModel.status).where(
                NotificationModel.project_id == UUID(project_id)
            )
        )
        return {(id_, status) for id_, status in rows}


async def project_count(session_maker: SessionMaker) -> int:
    async with session_maker() as session:
        return await session.scalar(select(func.count()).select_from(ProjectModel)) or 0


@pytest.fixture
async def owner(client: AsyncClient) -> Session:
    return await signed_up(client, username="owner")


@pytest.fixture
async def member(client: AsyncClient) -> Session:
    return await signed_up(client, username="member")


@pytest.fixture
async def stranger(client: AsyncClient) -> Session:
    return await signed_up(client, username="stranger")


@pytest.fixture
async def project(client: AsyncClient, owner: Session, member: Session) -> dict[str, Any]:
    """A project of `owner` with `member` as a collaborator."""
    created = await create_project(client, owner)
    await add_collaborator(client, created["id"], owner, "member")
    return created


# Every operation with a body that passes validation: errors come from access checks.
ENDPOINTS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "", None),
    ("PATCH", "", {"name": "Renamed"}),
    ("DELETE", "", None),
    ("PUT", "/reminders", {"offsets_days": [1]}),
    ("GET", "/collaborators", None),
    ("POST", "/collaborators", {"username": "owner"}),
    ("DELETE", "/collaborators/{user_id}", None),
]


async def test_every_operation_requires_a_token(client: AsyncClient) -> None:
    create = {"name": "Move", "moving_date": "2030-01-01", "time_zone": "UTC"}
    operations = [
        ("GET", "/projects", None),
        ("POST", "/projects", create),
        *((method, f"/projects/{uuid4()}{suffix}", body) for method, suffix, body in ENDPOINTS),
    ]
    found = []
    for method, path, body in operations:
        response = await client.request(
            method, f"{API}{path}".replace("{user_id}", str(uuid4())), json=body
        )
        found.append((method, path, response.status_code, error_code(response)))

    assert found == [(method, path, 401, "not_authenticated") for method, path, _ in operations]


class TestStrangers:
    async def test_existing_project_looks_like_a_missing_one(
        self, client: AsyncClient, project: dict[str, Any], owner: Session, stranger: Session
    ) -> None:
        def comparable(response: Response, project_id: str) -> dict[str, Any]:
            error = response.json()["error"]
            return {
                "status": response.status_code,
                "code": error["code"],
                "message": error["message"].replace(project_id, "<id>"),
                "details": error["details"],
            }

        wrong = []
        for method, suffix, body in ENDPOINTS:
            missing_id = str(uuid4())
            path = suffix.replace("{user_id}", owner.user_id)
            existing = await client.request(
                method, project_url(project["id"], path), json=body, headers=stranger.headers
            )
            missing = await client.request(
                method, project_url(missing_id, path), json=body, headers=stranger.headers
            )
            seen = comparable(existing, project["id"])
            if seen != comparable(missing, missing_id) or seen["code"] != "project_not_found":
                wrong.append((method, suffix, seen))

        assert wrong == []
        # Nothing changed for the members.
        unchanged = await client.get(project_url(project["id"]), headers=owner.headers)
        assert unchanged.json() == project

    async def test_malformed_ids_are_validation_errors(
        self, client: AsyncClient, project: dict[str, Any], owner: Session
    ) -> None:
        urls = [
            ("GET", f"{API}/projects/not-a-uuid"),
            ("DELETE", f"{API}/projects/not-a-uuid"),
            ("DELETE", project_url(project["id"], "/collaborators/not-a-uuid")),
        ]
        found = []
        for method, url in urls:
            response = await client.request(method, url, headers=owner.headers)
            found.append((response.status_code, error_code(response)))

        assert found == [(422, "validation_error")] * len(urls)


class TestCreate:
    async def test_full_body(
        self, client: AsyncClient, owner: Session, session_maker: SessionMaker
    ) -> None:
        body = {
            "name": "Переезд на Садовую",
            "moving_date": MOVING_DATE.isoformat(),
            "origin_address": "ул. Ленина, 14",
            "destination_address": "ул. Садовая, 8",
            "qr_code_strategy": "OPEN",
            "time_zone": "Europe/Moscow",
            "reminder_offsets_days": [7, 1],
            "rooms": {"source": ["Кухня", "Спальня"], "destination": ["Кухня", "Детская"]},
        }

        response = await client.post(f"{API}/projects", json=body, headers=owner.headers)

        assert response.status_code == 201
        created = response.json()
        assert created == {
            "id": created["id"],
            "name": "Переезд на Садовую",
            "moving_date": MOVING_DATE.isoformat(),
            "origin_address": "ул. Ленина, 14",
            "destination_address": "ул. Садовая, 8",
            "qr_code_strategy": "OPEN",
            "time_zone": "Europe/Moscow",
            "reminder_offsets_days": [1, 7],
            "owner": {"id": owner.user_id, "username": "owner"},
            "is_owner": True,
            "created_at": created["created_at"],
            "updated_at": created["updated_at"],
        }
        fetched = await client.get(project_url(created["id"]), headers=owner.headers)
        assert fetched.json() == created
        async with session_maker() as session:
            rooms = await session.execute(
                select(RoomModel.kind, RoomModel.name, RoomModel.is_active).where(
                    RoomModel.project_id == UUID(created["id"])
                )
            )
            audit = (await session.execute(select(AuditLogModel))).scalars().all()
        assert sorted(tuple(row) for row in rooms) == [
            ("DESTINATION", "Детская", True),
            ("DESTINATION", "Кухня", True),
            ("SOURCE", "Кухня", True),
            ("SOURCE", "Спальня", True),
        ]
        assert [
            (str(e.project_id), str(e.actor_id), e.action, e.entity_type, str(e.entity_id))
            for e in audit
        ] == [(created["id"], owner.user_id, "PROJECT_CREATED", "PROJECT", created["id"])]

    async def test_defaults(self, client: AsyncClient, owner: Session) -> None:
        created = await create_project(client, owner, name="  Move  ")

        assert created["name"] == "Move"  # trimmed by the domain
        assert created["origin_address"] is None
        assert created["destination_address"] is None
        assert created["qr_code_strategy"] == "PROTECTED"
        assert created["reminder_offsets_days"] == []

    async def test_queues_reminders_for_nine_local_time(
        self, client: AsyncClient, owner: Session, session_maker: SessionMaker
    ) -> None:
        # 09:00 where the move is, in UTC: no DST in these zones, so no surprises.
        cases: list[tuple[str, Callable[[date], datetime]]] = [
            ("Europe/Moscow", lambda day: utc(day, 6)),
            ("UTC", lambda day: utc(day, 9)),
            ("Asia/Vladivostok", lambda day: utc(day - timedelta(days=1), 23)),
        ]
        wrong = []
        for time_zone, expected in cases:
            created = await create_project(
                client, owner, time_zone=time_zone, reminder_offsets_days=[0, 7]
            )
            found = await moving_reminders(session_maker, created["id"])
            if found != [
                (owner.user_id, 0, expected(MOVING_DATE), "PENDING"),
                (owner.user_id, 7, expected(MOVING_DATE - timedelta(days=7)), "PENDING"),
            ]:
                wrong.append((time_zone, found))

        assert wrong == []

    async def test_reminders_already_due_are_kept_but_not_queued(
        self, client: AsyncClient, owner: Session, session_maker: SessionMaker
    ) -> None:
        soon = in_days(2)

        created = await create_project(
            client, owner, moving_date=soon.isoformat(), reminder_offsets_days=[0, 30]
        )

        assert created["reminder_offsets_days"] == [0, 30]
        assert await moving_reminders(session_maker, created["id"]) == [
            (owner.user_id, 0, utc(soon, 6), "PENDING")
        ]

    async def test_bounds_are_accepted(
        self, client: AsyncClient, owner: Session, session_maker: SessionMaker
    ) -> None:
        far = in_days(400)

        created = await create_project(
            client,
            owner,
            name="x" * 200,
            moving_date=far.isoformat(),
            reminder_offsets_days=[365, 0],
        )

        assert created["name"] == "x" * 200
        assert created["reminder_offsets_days"] == [0, 365]
        assert len(await moving_reminders(session_maker, created["id"])) == 2

    async def test_invalid_body_creates_nothing(
        self, client: AsyncClient, owner: Session, session_maker: SessionMaker
    ) -> None:
        cases: list[tuple[dict[str, Any], str]] = [
            ({"name": None}, "validation_error"),
            ({"time_zone": None}, "validation_error"),
            ({"moving_date": "2030-13-01"}, "validation_error"),
            ({"qr_code_strategy": "SECRET"}, "validation_error"),
            ({"reminder_offsets_days": ["soon"]}, "validation_error"),
            ({"rooms": {"attic": ["Attic"]}}, "validation_error"),
            ({"unknown": 1}, "validation_error"),
            ({"name": "   "}, "invalid_project_name"),
            ({"name": "x" * 201}, "invalid_project_name"),
            ({"origin_address": ""}, "invalid_address"),
            ({"destination_address": "x" * 501}, "invalid_address"),
            ({"time_zone": "Mars/Olympus_Mons"}, "invalid_time_zone"),
            ({"time_zone": "europe/moscow"}, "invalid_time_zone"),
            ({"reminder_offsets_days": [-1]}, "invalid_moving_reminder_offset"),
            ({"reminder_offsets_days": [1, 366]}, "invalid_moving_reminder_offset"),
            ({"rooms": {"source": [""]}}, "invalid_room_name"),
        ]
        valid = {"name": "Move", "moving_date": MOVING_DATE.isoformat(), "time_zone": "UTC"}
        wrong = []
        for overrides, code in cases:
            body = {key: value for key, value in (valid | overrides).items() if value is not None}
            response = await client.post(f"{API}/projects", json=body, headers=owner.headers)
            if (response.status_code, error_code(response)) != (422, code):
                wrong.append((overrides, response.status_code, response.json()))

        assert wrong == []
        assert await project_count(session_maker) == 0

    async def test_duplicate_room_names_of_one_kind_create_nothing(
        self, client: AsyncClient, owner: Session, session_maker: SessionMaker
    ) -> None:
        body = {
            "name": "Move",
            "moving_date": MOVING_DATE.isoformat(),
            "time_zone": "Europe/Moscow",
            "reminder_offsets_days": [7],
            "rooms": {"source": ["Kitchen", "Hall", "Kitchen"]},
        }

        response = await client.post(f"{API}/projects", json=body, headers=owner.headers)

        assert response.status_code == 409
        assert error_code(response) == "room_name_taken"
        assert await project_count(session_maker) == 0
        async with session_maker() as session:
            assert await session.scalar(select(func.count()).select_from(RoomModel)) == 0
            assert await session.scalar(select(func.count()).select_from(NotificationModel)) == 0
        listed = await client.get(f"{API}/projects", headers=owner.headers)
        assert listed.json() == []

    async def test_idempotency_key_replays_the_first_response(
        self, client: AsyncClient, owner: Session, stranger: Session, session_maker: SessionMaker
    ) -> None:
        body = {"name": "Move", "moving_date": MOVING_DATE.isoformat(), "time_zone": "UTC"}
        headers = owner.headers | {"Idempotency-Key": "create-project-1"}

        first = await client.post(f"{API}/projects", json=body, headers=headers)
        retry = await client.post(f"{API}/projects", json=body, headers=headers)
        reused = await client.post(
            f"{API}/projects", json=body | {"name": "Other"}, headers=headers
        )
        # Keys are scoped per user: another user with the same key gets their own project.
        foreign = await client.post(
            f"{API}/projects",
            json=body,
            headers=stranger.headers | {"Idempotency-Key": "create-project-1"},
        )

        assert (first.status_code, retry.status_code) == (201, 201)
        assert retry.json() == first.json()
        assert "Idempotent-Replayed" not in first.headers
        assert retry.headers["Idempotent-Replayed"] == "true"
        assert reused.status_code == 422
        assert error_code(reused) == "idempotency_key_reused"
        assert foreign.status_code == 201
        assert foreign.json()["id"] != first.json()["id"]
        assert await project_count(session_maker) == 2

    async def test_is_logged(
        self, client: AsyncClient, owner: Session, log_events: LogEvents
    ) -> None:
        created = await create_project(client, owner, rooms={"source": ["Kitchen"]})

        events = [e for e in log_events() if e["event"] == "project_created"]
        assert [(e["project_id"], e["user_id"], e["rooms"]) for e in events] == [
            (created["id"], owner.user_id, 1)
        ]


class TestList:
    async def test_member_projects_by_moving_date(
        self, client: AsyncClient, owner: Session, member: Session, stranger: Session
    ) -> None:
        later = await create_project(
            client, owner, name="Later", moving_date=in_days(90).isoformat()
        )
        sooner = await create_project(
            client, owner, name="Sooner", moving_date=in_days(30).isoformat()
        )
        foreign = await create_project(
            client, member, name="Foreign", moving_date=in_days(45).isoformat()
        )
        await add_collaborator(client, foreign["id"], member, "owner")
        await create_project(client, stranger, name="Hidden")

        response = await client.get(f"{API}/projects", headers=owner.headers)

        assert response.status_code == 200
        listed = response.json()
        assert [p["name"] for p in listed] == ["Sooner", "Foreign", "Later"]
        assert [p["is_owner"] for p in listed] == [True, False, True]
        assert listed[0] == sooner
        assert listed[2] == later
        assert listed[1]["owner"] == {"id": member.user_id, "username": "member"}

    async def test_same_moving_date_keeps_a_stable_order(
        self, client: AsyncClient, owner: Session
    ) -> None:
        ids = [(await create_project(client, owner, name=f"Move {n}"))["id"] for n in range(3)]

        response = await client.get(f"{API}/projects", headers=owner.headers)

        assert [p["id"] for p in response.json()] == sorted(ids)  # UUIDv7: by creation

    async def test_empty_and_deleted(self, client: AsyncClient, owner: Session) -> None:
        empty = await client.get(f"{API}/projects", headers=owner.headers)
        created = await create_project(client, owner)
        await client.delete(project_url(created["id"]), headers=owner.headers)

        after_delete = await client.get(f"{API}/projects", headers=owner.headers)

        assert empty.json() == []
        assert after_delete.json() == []


class TestGet:
    async def test_member_sees_the_project_but_does_not_own_it(
        self, client: AsyncClient, project: dict[str, Any], member: Session
    ) -> None:
        response = await client.get(project_url(project["id"]), headers=member.headers)

        assert response.status_code == 200
        assert response.json() == project | {"is_owner": False}


class TestUpdate:
    async def test_changes_every_field(
        self, client: AsyncClient, project: dict[str, Any], member: Session
    ) -> None:
        changes = {
            "name": "New name",
            "moving_date": in_days(70).isoformat(),
            "origin_address": "Old street, 1",
            "destination_address": "New street, 2",
            "qr_code_strategy": "OPEN",
            "time_zone": "Asia/Yekaterinburg",
        }

        # Any member may change the project, not only the owner.
        response = await client.patch(
            project_url(project["id"]), json=changes, headers=member.headers
        )

        assert response.status_code == 200
        updated = response.json()
        assert updated == project | changes | {
            "is_owner": False,
            "updated_at": updated["updated_at"],
        }
        assert updated["updated_at"] > project["updated_at"]
        fetched = await client.get(project_url(project["id"]), headers=member.headers)
        assert fetched.json() == updated

    async def test_empty_body_changes_nothing(
        self, client: AsyncClient, project: dict[str, Any], owner: Session
    ) -> None:
        response = await client.patch(project_url(project["id"]), json={}, headers=owner.headers)

        assert response.status_code == 200
        assert response.json() == project

    async def test_null_clears_an_address_and_omitted_fields_stay(
        self, client: AsyncClient, owner: Session
    ) -> None:
        created = await create_project(
            client, owner, origin_address="Old street, 1", destination_address="New street, 2"
        )

        response = await client.patch(
            project_url(created["id"]), json={"origin_address": None}, headers=owner.headers
        )

        updated = response.json()
        assert updated["origin_address"] is None
        assert updated["destination_address"] == "New street, 2"
        assert {
            k: updated[k] for k in ("name", "moving_date", "time_zone", "qr_code_strategy")
        } == {k: created[k] for k in ("name", "moving_date", "time_zone", "qr_code_strategy")}

    async def test_invalid_values_change_nothing(
        self, client: AsyncClient, project: dict[str, Any], owner: Session
    ) -> None:
        cases: list[tuple[dict[str, Any], str]] = [
            ({"name": None}, "validation_error"),
            ({"moving_date": None}, "validation_error"),
            ({"time_zone": None}, "validation_error"),
            ({"qr_code_strategy": None}, "validation_error"),
            ({"qr_code_strategy": "SECRET"}, "validation_error"),
            ({"moving_date": "tomorrow"}, "validation_error"),
            ({"owner_id": "01890000-0000-7000-8000-000000000000"}, "validation_error"),
            ({"name": ""}, "invalid_project_name"),
            ({"name": "x" * 201}, "invalid_project_name"),
            ({"origin_address": "x" * 501}, "invalid_address"),
            ({"time_zone": "Nowhere/Town"}, "invalid_time_zone"),
        ]
        wrong = []
        for body, code in cases:
            response = await client.patch(
                project_url(project["id"]), json=body, headers=owner.headers
            )
            if (response.status_code, error_code(response)) != (422, code):
                wrong.append((body, response.status_code, response.json()))

        assert wrong == []
        unchanged = await client.get(project_url(project["id"]), headers=owner.headers)
        assert unchanged.json() == project

    async def test_new_moving_date_moves_pending_reminders_of_every_member(
        self,
        client: AsyncClient,
        owner: Session,
        member: Session,
        session_maker: SessionMaker,
    ) -> None:
        created = await create_project(client, owner, reminder_offsets_days=[1])
        await add_collaborator(client, created["id"], owner, "member")
        new_date = in_days(80)

        response = await client.patch(
            project_url(created["id"]),
            json={"moving_date": new_date.isoformat()},
            headers=member.headers,
        )

        assert response.status_code == 200
        reminders = await moving_reminders(session_maker, created["id"])
        old_moment = utc(MOVING_DATE - timedelta(days=1), 6)
        new_moment = utc(new_date - timedelta(days=1), 6)
        assert with_status(reminders, "CANCELLED") == sorted(
            [
                (owner.user_id, 1, old_moment, "CANCELLED"),
                (member.user_id, 1, old_moment, "CANCELLED"),
            ]
        )
        assert with_status(reminders, "PENDING") == sorted(
            [(owner.user_id, 1, new_moment, "PENDING"), (member.user_id, 1, new_moment, "PENDING")]
        )

    async def test_new_time_zone_moves_pending_reminders(
        self, client: AsyncClient, owner: Session, session_maker: SessionMaker
    ) -> None:
        created = await create_project(client, owner, reminder_offsets_days=[0])

        await client.patch(
            project_url(created["id"]), json={"time_zone": "UTC"}, headers=owner.headers
        )

        assert await moving_reminders(session_maker, created["id"]) == [
            (owner.user_id, 0, utc(MOVING_DATE, 6), "CANCELLED"),
            (owner.user_id, 0, utc(MOVING_DATE, 9), "PENDING"),
        ]

    async def test_moving_date_in_the_past_leaves_no_pending_reminder(
        self, client: AsyncClient, owner: Session, session_maker: SessionMaker
    ) -> None:
        created = await create_project(client, owner, reminder_offsets_days=[0])

        await client.patch(
            project_url(created["id"]),
            json={"moving_date": in_days(-3).isoformat()},
            headers=owner.headers,
        )

        reminders = await moving_reminders(session_maker, created["id"])
        assert [status for *_, status in reminders] == ["CANCELLED"]

    async def test_other_changes_keep_reminders(
        self, client: AsyncClient, owner: Session, session_maker: SessionMaker
    ) -> None:
        created = await create_project(client, owner, reminder_offsets_days=[3])
        before = await notification_ids(session_maker, created["id"])

        await client.patch(
            project_url(created["id"]),
            # The same date and zone again is not a change either.
            json={
                "name": "Renamed",
                "qr_code_strategy": "OPEN",
                "moving_date": created["moving_date"],
                "time_zone": created["time_zone"],
            },
            headers=owner.headers,
        )

        assert await notification_ids(session_maker, created["id"]) == before


class TestDelete:
    async def test_owner_deletes_for_everybody(
        self,
        client: AsyncClient,
        owner: Session,
        member: Session,
        session_maker: SessionMaker,
    ) -> None:
        created = await create_project(client, owner, reminder_offsets_days=[1, 7])
        await add_collaborator(client, created["id"], owner, "member")

        response = await client.delete(project_url(created["id"]), headers=owner.headers)

        assert response.status_code == 204
        assert response.content == b""
        for viewer in (owner, member):
            gone = await client.get(project_url(created["id"]), headers=viewer.headers)
            assert gone.status_code == 404
            assert error_code(gone) == "project_not_found"
        again = await client.delete(project_url(created["id"]), headers=owner.headers)
        assert again.status_code == 404
        reminders = await moving_reminders(session_maker, created["id"])
        assert len(reminders) == 4
        assert with_status(reminders, "PENDING") == []
        async with session_maker() as session:
            stored = await session.get(ProjectModel, UUID(created["id"]))
        assert stored is not None
        assert stored.is_active is False  # soft-deleted, data kept

    async def test_other_members_may_not(
        self,
        client: AsyncClient,
        project: dict[str, Any],
        member: Session,
        log_events: LogEvents,
    ) -> None:
        response = await client.delete(project_url(project["id"]), headers=member.headers)

        assert response.status_code == 403
        assert error_code(response) == "not_project_owner"
        still_there = await client.get(project_url(project["id"]), headers=member.headers)
        assert still_there.status_code == 200
        warnings = [e for e in log_events() if e["event"] == "project_delete_forbidden"]
        assert [(e["project_id"], e["user_id"], e["level"]) for e in warnings] == [
            (project["id"], member.user_id, "warning")
        ]


class TestReminders:
    async def test_replace_cancels_removed_and_queues_added_for_every_member(
        self,
        client: AsyncClient,
        owner: Session,
        member: Session,
        session_maker: SessionMaker,
    ) -> None:
        created = await create_project(client, owner, reminder_offsets_days=[7, 1])
        await add_collaborator(client, created["id"], owner, "member")

        response = await client.put(
            project_url(created["id"], "/reminders"),
            json={"offsets_days": [3, 1, 3]},
            headers=member.headers,
        )

        assert response.status_code == 200
        assert response.json()["reminder_offsets_days"] == [1, 3]

        def at(offset: int) -> datetime:
            return utc(MOVING_DATE - timedelta(days=offset), 6)

        assert await moving_reminders(session_maker, created["id"]) == sorted(
            [
                # Offset 1 is kept as it was; the member joined after it was queued.
                (owner.user_id, 1, at(1), "PENDING"),
                (member.user_id, 1, at(1), "PENDING"),
                (owner.user_id, 7, at(7), "CANCELLED"),
                (member.user_id, 7, at(7), "CANCELLED"),
                (owner.user_id, 3, at(3), "PENDING"),
                (member.user_id, 3, at(3), "PENDING"),
            ]
        )

    async def test_same_set_writes_nothing(
        self, client: AsyncClient, owner: Session, session_maker: SessionMaker
    ) -> None:
        created = await create_project(client, owner, reminder_offsets_days=[7, 1])
        before = await notification_ids(session_maker, created["id"])

        response = await client.put(
            project_url(created["id"], "/reminders"),
            json={"offsets_days": [1, 7]},
            headers=owner.headers,
        )

        assert response.json() == created
        assert await notification_ids(session_maker, created["id"]) == before

    async def test_empty_set_cancels_everything(
        self, client: AsyncClient, owner: Session, session_maker: SessionMaker
    ) -> None:
        created = await create_project(client, owner, reminder_offsets_days=[7, 1])

        response = await client.put(
            project_url(created["id"], "/reminders"),
            json={"offsets_days": []},
            headers=owner.headers,
        )

        assert response.json()["reminder_offsets_days"] == []
        reminders = await moving_reminders(session_maker, created["id"])
        assert [status for *_, status in reminders] == ["CANCELLED", "CANCELLED"]

    async def test_bounds_are_accepted(
        self, client: AsyncClient, owner: Session, session_maker: SessionMaker
    ) -> None:
        created = await create_project(client, owner, moving_date=in_days(400).isoformat())

        response = await client.put(
            project_url(created["id"], "/reminders"),
            json={"offsets_days": [0, 365]},
            headers=owner.headers,
        )

        assert response.status_code == 200
        assert response.json()["reminder_offsets_days"] == [0, 365]
        assert (
            len(with_status(await moving_reminders(session_maker, created["id"]), "PENDING")) == 2
        )

    async def test_invalid_offsets_change_nothing(
        self, client: AsyncClient, owner: Session, session_maker: SessionMaker
    ) -> None:
        cases: list[tuple[dict[str, Any], str]] = [
            ({"offsets_days": [-1]}, "invalid_moving_reminder_offset"),
            ({"offsets_days": [1, 366]}, "invalid_moving_reminder_offset"),
            ({"offsets_days": ["week"]}, "validation_error"),
            ({"offsets_days": None}, "validation_error"),
            ({}, "validation_error"),
        ]
        created = await create_project(client, owner, reminder_offsets_days=[7])
        before = await notification_ids(session_maker, created["id"])
        wrong = []
        for body, code in cases:
            response = await client.put(
                project_url(created["id"], "/reminders"), json=body, headers=owner.headers
            )
            if (response.status_code, error_code(response)) != (422, code):
                wrong.append((body, response.status_code, response.json()))

        assert wrong == []
        unchanged = await client.get(project_url(created["id"]), headers=owner.headers)
        assert unchanged.json()["reminder_offsets_days"] == [7]
        assert await notification_ids(session_maker, created["id"]) == before


class TestCollaborators:
    async def test_owner_first_then_by_joining_time(
        self, client: AsyncClient, owner: Session, member: Session
    ) -> None:
        await signed_up(client, username="alice")
        created = await create_project(client, owner)
        await add_collaborator(client, created["id"], owner, "member")
        await add_collaborator(client, created["id"], member, "alice")

        response = await client.get(
            project_url(created["id"], "/collaborators"), headers=member.headers
        )

        assert response.status_code == 200
        listed = response.json()
        assert [(c["user"]["username"], c["is_owner"]) for c in listed] == [
            ("owner", True),
            ("member", False),
            ("alice", False),
        ]
        assert listed[0]["user"]["id"] == owner.user_id
        assert listed[0]["created_at"] < listed[1]["created_at"] < listed[2]["created_at"]

    async def test_add_new_member(
        self,
        client: AsyncClient,
        owner: Session,
        member: Session,
        session_maker: SessionMaker,
    ) -> None:
        created = await create_project(client, owner, reminder_offsets_days=[2])

        response = await client.post(
            project_url(created["id"], "/collaborators"),
            json={"username": "  Member "},  # normalized like at login
            headers=owner.headers,
        )

        assert response.status_code == 201
        added = response.json()
        assert added == {
            "user": {"id": member.user_id, "username": "member"},
            "is_owner": False,
            "created_at": added["created_at"],
        }
        seen = await client.get(project_url(created["id"]), headers=member.headers)
        assert seen.json() == created | {"is_owner": False}
        assert with_status(
            await moving_reminders(session_maker, created["id"]), "PENDING"
        ) == sorted(
            [
                (owner.user_id, 2, utc(MOVING_DATE - timedelta(days=2), 6), "PENDING"),
                (member.user_id, 2, utc(MOVING_DATE - timedelta(days=2), 6), "PENDING"),
            ]
        )

    async def test_adding_a_member_again_is_a_no_op(
        self,
        client: AsyncClient,
        project: dict[str, Any],
        owner: Session,
        session_maker: SessionMaker,
    ) -> None:
        listed = await client.get(
            project_url(project["id"], "/collaborators"), headers=owner.headers
        )
        before = await notification_ids(session_maker, project["id"])

        again = await client.post(
            project_url(project["id"], "/collaborators"),
            json={"username": "member"},
            headers=owner.headers,
        )
        oneself = await client.post(
            project_url(project["id"], "/collaborators"),
            json={"username": "owner"},
            headers=owner.headers,
        )

        assert (again.status_code, oneself.status_code) == (200, 200)
        assert again.json() == listed.json()[1]
        assert oneself.json() == listed.json()[0]
        assert await notification_ids(session_maker, project["id"]) == before

    async def test_add_errors(
        self, client: AsyncClient, project: dict[str, Any], owner: Session
    ) -> None:
        cases: list[tuple[dict[str, Any], int, str]] = [
            ({"username": "nobody"}, 404, "user_not_found"),
            ({"username": "no spaces!"}, 422, "invalid_username"),
            ({"username": "ab"}, 422, "invalid_username"),
            ({"username": None}, 422, "validation_error"),
            ({}, 422, "validation_error"),
        ]
        found = []
        for body, _, _ in cases:
            response = await client.post(
                project_url(project["id"], "/collaborators"), json=body, headers=owner.headers
            )
            found.append((body, response.status_code, error_code(response)))

        assert found == cases

    async def test_deleted_account_cannot_be_added(
        self, client: AsyncClient, owner: Session, stranger: Session
    ) -> None:
        created = await create_project(client, owner)
        deleted = await client.delete(f"{API}/users/me", headers=stranger.headers)
        assert deleted.status_code == 204

        response = await client.post(
            project_url(created["id"], "/collaborators"),
            json={"username": "stranger"},
            headers=owner.headers,
        )

        assert response.status_code == 404
        assert error_code(response) == "user_not_found"

    async def test_deleted_account_is_hidden(
        self, client: AsyncClient, project: dict[str, Any], owner: Session, member: Session
    ) -> None:
        deleted = await client.delete(f"{API}/users/me", headers=member.headers)
        assert deleted.status_code == 204

        response = await client.get(
            project_url(project["id"], "/collaborators"), headers=owner.headers
        )

        assert [c["user"]["username"] for c in response.json()] == ["owner"]

    async def test_remove_member_cancels_only_their_notifications(
        self,
        client: AsyncClient,
        owner: Session,
        member: Session,
        session_maker: SessionMaker,
        log_events: LogEvents,
    ) -> None:
        created = await create_project(client, owner, reminder_offsets_days=[1])
        await add_collaborator(client, created["id"], owner, "member")

        response = await client.delete(
            project_url(created["id"], f"/collaborators/{member.user_id}"), headers=owner.headers
        )

        assert response.status_code == 204
        gone = await client.get(project_url(created["id"]), headers=member.headers)
        assert gone.status_code == 404
        listed = await client.get(
            project_url(created["id"], "/collaborators"), headers=owner.headers
        )
        assert [c["user"]["id"] for c in listed.json()] == [owner.user_id]
        moment = utc(MOVING_DATE - timedelta(days=1), 6)
        assert await moving_reminders(session_maker, created["id"]) == sorted(
            [(owner.user_id, 1, moment, "PENDING"), (member.user_id, 1, moment, "CANCELLED")]
        )
        events = [e for e in log_events() if e["event"] == "collaborator_removed"]
        assert [e["collaborator_id"] for e in events] == [member.user_id]

    async def test_member_leaves_with_their_own_id(
        self,
        client: AsyncClient,
        project: dict[str, Any],
        member: Session,
        log_events: LogEvents,
    ) -> None:
        response = await client.delete(
            project_url(project["id"], f"/collaborators/{member.user_id}"), headers=member.headers
        )

        assert response.status_code == 204
        listed = await client.get(f"{API}/projects", headers=member.headers)
        assert listed.json() == []
        assert [e["user_id"] for e in log_events() if e["event"] == "project_left"] == [
            member.user_id
        ]

    async def test_any_member_may_remove_another(
        self, client: AsyncClient, project: dict[str, Any], owner: Session, member: Session
    ) -> None:
        other = await signed_up(client, username="other")
        await add_collaborator(client, project["id"], owner, "other")

        response = await client.delete(
            project_url(project["id"], f"/collaborators/{other.user_id}"), headers=member.headers
        )

        assert response.status_code == 204
        gone = await client.get(project_url(project["id"]), headers=other.headers)
        assert gone.status_code == 404

    async def test_owner_cannot_be_removed(
        self, client: AsyncClient, project: dict[str, Any], owner: Session, member: Session
    ) -> None:
        found = []
        for caller in (owner, member):
            response = await client.delete(
                project_url(project["id"], f"/collaborators/{owner.user_id}"),
                headers=caller.headers,
            )
            found.append((response.status_code, error_code(response)))

        assert found == [(409, "cannot_remove_owner")] * 2
        listed = await client.get(
            project_url(project["id"], "/collaborators"), headers=owner.headers
        )
        assert len(listed.json()) == 2

    async def test_removing_a_non_member_is_a_no_op(
        self, client: AsyncClient, project: dict[str, Any], owner: Session, stranger: Session
    ) -> None:
        statuses = []
        for user_id in (stranger.user_id, str(uuid4())):
            response = await client.delete(
                project_url(project["id"], f"/collaborators/{user_id}"), headers=owner.headers
            )
            statuses.append(response.status_code)

        assert statuses == [204, 204]
        listed = await client.get(
            project_url(project["id"], "/collaborators"), headers=owner.headers
        )
        assert len(listed.json()) == 2


class TestAccountDeletion:
    async def test_owner_deletes_projects_first(self, client: AsyncClient, owner: Session) -> None:
        created = await create_project(client, owner)

        refused = await client.delete(f"{API}/users/me", headers=owner.headers)
        await client.delete(project_url(created["id"]), headers=owner.headers)
        deleted = await client.delete(f"{API}/users/me", headers=owner.headers)

        assert refused.status_code == 409
        assert error_code(refused) == "user_owns_projects"
        assert deleted.status_code == 204  # the refused account was kept
