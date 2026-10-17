"""Tasks and task templates end to end: HTTP contract plus what lands in the database."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.models import AuditLogModel, NotificationModel
from tests.integration.helpers import API, Session, add_collaborator, create_project, signed_up

type SessionMaker = async_sessionmaker[AsyncSession]
type Queued = tuple[str, str, int | None]


def at(hours: int) -> str:
    """A moment `hours` from now, as clients send it."""
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


@dataclass(frozen=True, slots=True)
class Plan:
    client: AsyncClient
    owner: Session
    member: Session
    stranger: Session
    project_id: str

    async def call(
        self, method: str, path: str, *, as_: Session | None = None, json: Any = None
    ) -> Response:
        who = as_ or self.owner
        return await self.client.request(method, f"{API}{path}", json=json, headers=who.headers)

    async def ok(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self.call(method, path, **kwargs)
        assert response.status_code in {200, 201}, response.text
        return response.json()

    async def task(self, **body: Any) -> dict[str, Any]:
        task: dict[str, Any] = await self.ok(
            "POST", f"/projects/{self.project_id}/tasks", json={"title": "Pack"} | body
        )
        return task


@pytest.fixture
async def plan(client: AsyncClient) -> Plan:
    owner = await signed_up(client, username="owner")
    member = await signed_up(client, username="member")
    stranger = await signed_up(client, username="stranger")
    project = await create_project(client, owner)
    await add_collaborator(client, project["id"], owner, "member")
    return Plan(client, owner, member, stranger, project["id"])


def error(response: Response) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


async def queued(session_maker: SessionMaker, plan: Plan, task_id: str) -> list[Queued]:
    """Pending notifications about the task: (recipient, type, reminder offset)."""
    names = {plan.owner.user_id: "owner", plan.member.user_id: "member"}
    n = NotificationModel
    async with session_maker() as session:
        rows = await session.execute(
            select(n.user_id, n.type, n.reminder_offset_hours).where(
                n.task_id == UUID(task_id), n.status == "PENDING"
            )
        )
        return sorted(
            ((names[str(user_id)], type_, offset) for user_id, type_, offset in rows),
            key=lambda q: (q[0], q[1], q[2] or 0),
        )


async def audit_actions(session_maker: SessionMaker, task_id: str) -> list[str]:
    async with session_maker() as session:
        rows = await session.scalars(
            select(AuditLogModel.action)
            .where(AuditLogModel.entity_id == UUID(task_id))
            .order_by(AuditLogModel.created_at, AuditLogModel.id)
        )
        return list(rows)


class TestAccess:
    async def test_every_operation_requires_a_token(self, client: AsyncClient) -> None:
        task, project = uuid4(), uuid4()
        operations = [
            ("GET", "/task-templates"),
            ("POST", f"/projects/{project}/tasks/from-templates"),
            ("GET", f"/projects/{project}/tasks"),
            ("POST", f"/projects/{project}/tasks"),
            ("GET", f"/tasks/{task}"),
            ("PATCH", f"/tasks/{task}"),
            ("POST", f"/tasks/{task}/complete"),
            ("POST", f"/tasks/{task}/reopen"),
            ("PUT", f"/tasks/{task}/assignees"),
            ("PUT", f"/tasks/{task}/reminders"),
            ("DELETE", f"/tasks/{task}"),
        ]
        found = [
            (method, path, error(await client.request(method, f"{API}{path}", json={})))
            for method, path in operations
        ]

        assert found == [(m, p, (401, "not_authenticated")) for m, p in operations]

    async def test_strangers_see_neither_the_project_nor_its_tasks(self, plan: Plan) -> None:
        task = await plan.task()
        requests: list[tuple[str, str, Any, tuple[int, str]]] = [
            ("GET", f"/projects/{plan.project_id}/tasks", None, (404, "project_not_found")),
            (
                "POST",
                f"/projects/{plan.project_id}/tasks",
                {"title": "X"},
                (404, "project_not_found"),
            ),
            (
                "POST",
                f"/projects/{plan.project_id}/tasks/from-templates",
                {"template_ids": ["book-movers"]},
                (404, "project_not_found"),
            ),
            ("GET", f"/tasks/{task['id']}", None, (404, "task_not_found")),
            ("PATCH", f"/tasks/{task['id']}", {"title": "X"}, (404, "task_not_found")),
            ("POST", f"/tasks/{task['id']}/complete", None, (404, "task_not_found")),
            ("POST", f"/tasks/{task['id']}/reopen", None, (404, "task_not_found")),
            ("PUT", f"/tasks/{task['id']}/assignees", {"user_ids": []}, (404, "task_not_found")),
            (
                "PUT",
                f"/tasks/{task['id']}/reminders",
                {"offsets_hours": []},
                (404, "task_not_found"),
            ),
            ("DELETE", f"/tasks/{task['id']}", None, (404, "task_not_found")),
        ]
        found = [
            error(await plan.call(method, path, as_=plan.stranger, json=body))
            for method, path, body, _ in requests
        ]

        assert found == [expected for *_, expected in requests]
        assert await plan.ok("GET", f"/tasks/{task['id']}") == task, "nothing changed"


class TestTemplates:
    async def test_list_and_copy_the_chosen_ones(
        self, plan: Plan, session_maker: SessionMaker
    ) -> None:
        templates = await plan.ok("GET", "/task-templates", as_=plan.stranger)
        phases = [template["phase"] for template in templates]
        chosen = ["photograph-meters", "notify-management-company", "photograph-meters"]

        created = await plan.ok(
            "POST",
            f"/projects/{plan.project_id}/tasks/from-templates",
            as_=plan.member,
            json={"template_ids": chosen},
        )

        assert phases == sorted(phases, key=["BEFORE", "DURING", "AFTER"].index)
        by_id = {template["id"]: template for template in templates}
        assert [(t["title"], t["phase"], t["description"]) for t in created] == [
            (by_id[i]["title"], by_id[i]["phase"], by_id[i]["description"]) for i in chosen[:2]
        ]
        assert all(t["due_at"] is None and t["assignees"] == [] for t in created)
        assert await audit_actions(session_maker, created[0]["id"]) == ["TASK_CREATED"]

    async def test_unknown_or_no_template_creates_nothing(self, plan: Plan) -> None:
        path = f"/projects/{plan.project_id}/tasks/from-templates"
        bodies = [
            ({"template_ids": ["book-movers", "no-such-template"]}, (422, "unknown_task_template")),
            ({"template_ids": []}, (422, "validation_error")),
            ({}, (422, "validation_error")),
        ]

        found = [error(await plan.call("POST", path, json=body)) for body, _ in bodies]

        assert found == [expected for _, expected in bodies]
        assert await plan.ok("GET", f"/projects/{plan.project_id}/tasks") == []


class TestCreateAndRead:
    async def test_full_body(self, plan: Plan, session_maker: SessionMaker) -> None:
        due = at(72)

        task = await plan.task(
            title="  Order movers ",
            description="Two companies",
            phase="DURING",
            due_at=due,
            assignee_ids=[plan.owner.user_id, plan.member.user_id],
            reminder_offsets_hours=[24, 1],
        )

        assert task == {
            "id": task["id"],
            "title": "Order movers",
            "description": "Two companies",
            "phase": "DURING",
            "due_at": task["due_at"],
            "is_done": False,
            "done_at": None,
            "assignees": [
                {"id": plan.member.user_id, "username": "member"},
                {"id": plan.owner.user_id, "username": "owner"},
            ],
            "reminder_offsets_hours": [1, 24],
            "created_at": task["created_at"],
            "updated_at": task["updated_at"],
        }
        assert datetime.fromisoformat(task["due_at"]) == datetime.fromisoformat(due)
        assert await plan.ok("GET", f"/tasks/{task['id']}", as_=plan.member) == task
        # The owner assigned themselves: only the member is told.
        assert await queued(session_maker, plan, task["id"]) == [
            ("member", "TASK_ASSIGNED", None),
            ("member", "TASK_REMINDER", 1),
            ("member", "TASK_REMINDER", 24),
            ("owner", "TASK_REMINDER", 1),
            ("owner", "TASK_REMINDER", 24),
        ]
        assert await audit_actions(session_maker, task["id"]) == ["TASK_CREATED"]

    async def test_list_in_plan_order_with_filters(self, plan: Plan) -> None:
        after = await plan.task(title="After", phase="AFTER")
        await plan.task(title="Undated")
        await plan.task(title="Later", due_at=at(48))
        sooner = await plan.task(title="Sooner", due_at=at(24))
        await plan.ok("POST", f"/tasks/{sooner['id']}/complete")
        path = f"/projects/{plan.project_id}/tasks"

        async def titles(query: str = "") -> list[str]:
            return [task["title"] for task in await plan.ok("GET", f"{path}{query}")]

        assert await titles() == ["Sooner", "Later", "Undated", "After"]
        assert await titles("?phase=AFTER") == [after["title"]]
        assert await titles("?is_done=true") == ["Sooner"]
        assert await titles("?is_done=false&phase=BEFORE") == ["Later", "Undated"]
        assert error(await plan.call("GET", f"{path}?phase=SOON")) == (422, "validation_error")

    async def test_invalid_bodies_create_nothing(self, plan: Plan) -> None:
        cases = [
            ({}, (422, "validation_error")),
            ({"title": "X", "due_at": "2030-01-01T10:00:00"}, (422, "validation_error")),
            ({"title": "X", "phase": "SOMEDAY"}, (422, "validation_error")),
            ({"title": "X", "unknown": 1}, (422, "validation_error")),
            (
                {"title": "X", "reminder_offsets_hours": list(range(1, 22))},
                (422, "validation_error"),
            ),
            ({"title": "  "}, (422, "invalid_task_title")),
            ({"title": "x" * 201}, (422, "invalid_task_title")),
            ({"title": "X", "assignee_ids": [str(uuid4())]}, (422, "assignee_not_collaborator")),
            ({"title": "X", "reminder_offsets_hours": [24]}, (409, "reminder_without_due_date")),
            (
                {"title": "X", "due_at": at(48), "reminder_offsets_hours": [8761]},
                (422, "invalid_reminder_offset"),
            ),
        ]
        path = f"/projects/{plan.project_id}/tasks"

        found = [error(await plan.call("POST", path, json=body)) for body, _ in cases]

        assert found == [expected for _, expected in cases]
        assert await plan.ok("GET", path) == []

    async def test_bounds_are_accepted(self, plan: Plan) -> None:
        task = await plan.task(title="x" * 200, due_at=at(9000), reminder_offsets_hours=[1, 8760])

        assert (len(task["title"]), task["reminder_offsets_hours"]) == (200, [1, 8760])


class TestChange:
    async def test_patch_is_partial_and_due_time_moves_reminders(
        self, plan: Plan, session_maker: SessionMaker
    ) -> None:
        task = await plan.task(
            description="Keep me",
            due_at=at(48),
            assignee_ids=[plan.member.user_id],
            reminder_offsets_hours=[24],
        )
        path = f"/tasks/{task['id']}"

        renamed = await plan.ok("PATCH", path, as_=plan.member, json={"title": "Renamed"})
        moved = await plan.ok("PATCH", path, json={"due_at": at(96), "phase": "AFTER"})
        rejected = [
            error(await plan.call("PATCH", path, json=body))
            for body in ({"title": None}, {"phase": None}, {"due_at": "tomorrow"})
        ]
        cleared = await plan.ok("PATCH", path, json={"due_at": None, "description": None})

        assert (renamed["title"], renamed["description"]) == ("Renamed", "Keep me")
        assert (moved["phase"], moved["reminder_offsets_hours"]) == ("AFTER", [24])
        assert rejected == [(422, "validation_error")] * 3
        assert (cleared["due_at"], cleared["description"], cleared["reminder_offsets_hours"]) == (
            None,
            None,
            [],
        )
        assert await queued(session_maker, plan, task["id"]) == [("member", "TASK_ASSIGNED", None)]

    async def test_complete_and_reopen_are_idempotent(
        self, plan: Plan, session_maker: SessionMaker
    ) -> None:
        task = await plan.task(
            due_at=at(48), assignee_ids=[plan.member.user_id], reminder_offsets_hours=[24]
        )
        path = f"/tasks/{task['id']}"

        done = await plan.ok("POST", f"{path}/complete")
        done_again = await plan.ok("POST", f"{path}/complete", as_=plan.member)
        nothing_pending = await queued(session_maker, plan, task["id"])
        reopened = await plan.ok("POST", f"{path}/reopen")
        reopened_again = await plan.ok("POST", f"{path}/reopen")

        assert done["is_done"]
        assert done["done_at"] is not None
        assert done_again["done_at"] == done["done_at"], "the first completion is kept"
        assert nothing_pending == []
        assert (reopened["is_done"], reopened_again["done_at"]) == (False, None)
        assert await queued(session_maker, plan, task["id"]) == [("member", "TASK_REMINDER", 24)]
        assert await audit_actions(session_maker, task["id"]) == [
            "TASK_CREATED",
            "TASK_COMPLETED",
            "TASK_REOPENED",
        ]

    async def test_assignees_and_reminders_are_replaced(
        self, plan: Plan, session_maker: SessionMaker
    ) -> None:
        task = await plan.task(
            due_at=at(48), assignee_ids=[plan.owner.user_id], reminder_offsets_hours=[24]
        )
        path = f"/tasks/{task['id']}"

        assigned = await plan.ok(
            "PUT", f"{path}/assignees", json={"user_ids": [plan.member.user_id]}
        )
        reminded = await plan.ok("PUT", f"{path}/reminders", json={"offsets_hours": [2, 2, 6]})
        rejected = [
            error(
                await plan.call(
                    "PUT", f"{path}/assignees", json={"user_ids": [plan.stranger.user_id]}
                )
            ),
            error(await plan.call("PUT", f"{path}/reminders", json={"offsets_hours": [0]})),
            error(await plan.call("PUT", f"{path}/reminders", json={})),
        ]

        assert [user["username"] for user in assigned["assignees"]] == ["member"]
        assert reminded["reminder_offsets_hours"] == [2, 6]
        assert rejected == [
            (422, "assignee_not_collaborator"),
            (422, "invalid_reminder_offset"),
            (422, "validation_error"),
        ]
        assert await queued(session_maker, plan, task["id"]) == [
            ("member", "TASK_ASSIGNED", None),
            ("member", "TASK_REMINDER", 2),
            ("member", "TASK_REMINDER", 6),
        ]

    async def test_a_member_who_leaves_loses_their_assignments(
        self, plan: Plan, session_maker: SessionMaker
    ) -> None:
        task = await plan.task(due_at=at(48), assignee_ids=[plan.member.user_id])

        left = await plan.call(
            "DELETE",
            f"/projects/{plan.project_id}/collaborators/{plan.member.user_id}",
            as_=plan.member,
        )

        assert left.status_code == 204
        assert (await plan.ok("GET", f"/tasks/{task['id']}"))["assignees"] == []
        assert await queued(session_maker, plan, task["id"]) == []


async def test_delete(plan: Plan, session_maker: SessionMaker) -> None:
    task = await plan.task(assignee_ids=[plan.member.user_id])
    path = f"/tasks/{task['id']}"

    deleted = await plan.call("DELETE", path, as_=plan.member)
    again = await plan.call("DELETE", path)

    assert (deleted.status_code, deleted.content) == (204, b"")
    assert error(again) == (404, "task_not_found")
    assert error(await plan.call("GET", path)) == (404, "task_not_found")
    assert await plan.ok("GET", f"/projects/{plan.project_id}/tasks") == []
    assert await queued(session_maker, plan, task["id"]) == []
