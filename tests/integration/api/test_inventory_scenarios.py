"""The inventory end to end: rooms, boxes, items and QR codes over HTTP and PostgreSQL."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.common.ids import new_id
from app.domain.projects.value_objects import QrCodeStrategy
from app.infrastructure.db.models import AuditLogModel, NotificationModel
from tests.integration.helpers import API, Session, add_collaborator, create_project, signed_up

type SessionMaker = async_sessionmaker[AsyncSession]


@dataclass(frozen=True, slots=True)
class Move:
    """A project of `owner` (with `member`), a kitchen to pack and a kitchen to unpack."""

    client: AsyncClient
    owner: Session
    member: Session
    stranger: Session
    project_id: str
    source_id: str
    destination_id: str

    async def call(
        self, method: str, path: str, *, as_: Session | None = None, **kwargs: Any
    ) -> Response:
        headers = (as_ or self.owner).headers
        return await self.client.request(method, f"{API}{path}", headers=headers, **kwargs)

    async def ok(self, method: str, path: str, *, status: int = 200, **kwargs: Any) -> Any:
        response = await self.call(method, path, **kwargs)
        assert response.status_code == status, response.text
        return response.json() if response.content else None

    async def box(self, **body: Any) -> dict[str, Any]:
        payload = {"destination_room_id": self.destination_id} | body
        box: dict[str, Any] = await self.ok(
            "POST", f"/projects/{self.project_id}/boxes", json=payload, status=201
        )
        return box

    async def item(self, name: str = "Чайник", **body: Any) -> dict[str, Any]:
        payload = {"name": name, "source_room_id": self.source_id} | body
        item: dict[str, Any] = await self.ok(
            "POST", f"/projects/{self.project_id}/items", json=payload, status=201
        )
        return item


@pytest.fixture
async def move(client: AsyncClient) -> Move:
    owner = await signed_up(client, username="owner")
    member = await signed_up(client, username="member")
    stranger = await signed_up(client, username="stranger")
    project_id = (await create_project(client, owner))["id"]
    await add_collaborator(client, project_id, owner, "member")
    rooms = f"{API}/projects/{project_id}/rooms"
    source = await client.post(
        rooms, json={"kind": "SOURCE", "name": "Кухня"}, headers=owner.headers
    )
    destination = await client.post(
        rooms, json={"kind": "DESTINATION", "name": "Кухня"}, headers=owner.headers
    )
    return Move(
        client,
        owner,
        member,
        stranger,
        project_id,
        source.json()["id"],
        destination.json()["id"],
    )


async def audit_actions(session_maker: SessionMaker, entity_type: str) -> list[str]:
    """Journal actions about one kind of object (creating the project is logged too)."""
    async with session_maker() as session:
        rows = await session.scalars(
            select(AuditLogModel.action)
            .where(AuditLogModel.entity_type == entity_type)
            .order_by(AuditLogModel.created_at)
        )
        return list(rows)


class TestRooms:
    async def test_create_list_rename(self, move: Move) -> None:
        hall = await move.ok(
            "POST",
            f"/projects/{move.project_id}/rooms",
            json={"kind": "SOURCE", "name": "  Прихожая "},
            status=201,
        )
        await move.ok("PATCH", f"/rooms/{hall['id']}", json={"name": "Коридор"})

        sources = await move.ok(
            "GET", f"/projects/{move.project_id}/rooms", params={"kind": "SOURCE"}
        )
        both = await move.ok("GET", f"/projects/{move.project_id}/rooms")

        assert [room["name"] for room in sources] == ["Коридор", "Кухня"]
        assert len(both) == 3

    async def test_name_is_unique_per_kind_among_active(self, move: Move) -> None:
        response = await move.call(
            "POST", f"/projects/{move.project_id}/rooms", json={"kind": "SOURCE", "name": "Кухня"}
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "room_name_taken"

    async def test_a_room_in_use_cannot_be_deleted(self, move: Move) -> None:
        await move.item()

        response = await move.call("DELETE", f"/rooms/{move.source_id}")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "room_in_use"

    async def test_an_unused_room_is_deleted_and_frees_its_name(self, move: Move) -> None:
        await move.ok("DELETE", f"/rooms/{move.source_id}", status=204)

        recreated = await move.call(
            "POST", f"/projects/{move.project_id}/rooms", json={"kind": "SOURCE", "name": "Кухня"}
        )
        assert recreated.status_code == 201

    async def test_strangers_do_not_see_the_project(self, move: Move) -> None:
        listing = await move.call("GET", f"/projects/{move.project_id}/rooms", as_=move.stranger)
        renaming = await move.call(
            "PATCH", f"/rooms/{move.source_id}", json={"name": "Моя"}, as_=move.stranger
        )

        assert (listing.status_code, listing.json()["error"]["code"]) == (404, "project_not_found")
        assert (renaming.status_code, renaming.json()["error"]["code"]) == (404, "room_not_found")


class TestBoxes:
    async def test_create_with_items(self, move: Move) -> None:
        box = await move.box(
            title="Посуда",
            is_fragile=True,
            items={
                "source_room_id": move.source_id,
                "priority": "FIRST_NECESSITY",
                "entries": [
                    {"name": "Кружки", "quantity": 6},
                    {"name": "Чайник"},
                    {"name": "Аптечка"},
                    {"name": "Ложки"},
                ],
            },
        )

        assert box["number"] == 1
        assert box["status"] == "PACKING"
        assert (box["title"], box["is_fragile"]) == ("Посуда", True)
        assert box["destination_room"] == {"id": move.destination_id, "name": "Кухня"}
        assert box["created_by"] == {"id": move.owner.user_id, "username": "owner"}
        assert box["item_count"] == 4
        assert box["items_preview"] == ["Аптечка", "Кружки", "Ложки"]  # packing order
        assert len(box["qr_token"]) == 22

    async def test_numbers_are_the_lowest_free_among_active_boxes(self, move: Move) -> None:
        first, second, _ = [await move.box() for _ in range(3)]
        await move.ok("DELETE", f"/boxes/{second['id']}", status=204)

        fourth = await move.box()

        assert (first["number"], fourth["number"]) == (1, 2)

    async def test_a_destination_must_be_a_destination_room(self, move: Move) -> None:
        response = await move.call(
            "POST",
            f"/projects/{move.project_id}/boxes",
            json={"destination_room_id": move.source_id},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "wrong_room_kind"

    async def test_list_is_paged_by_number_with_filters(self, move: Move) -> None:
        boxes = [await move.box() for _ in range(3)]
        await move.ok("PUT", f"/boxes/{boxes[1]['id']}/status", json={"status": "PACKED"})
        path = f"/projects/{move.project_id}/boxes"

        first = await move.ok("GET", path, params={"limit": 2})
        rest = await move.ok("GET", path, params={"limit": 2, "after": first["next_cursor"]})
        packed = await move.ok("GET", path, params={"status": "PACKED"})
        by_number = await move.ok("GET", path, params={"number": 3})

        assert [box["number"] for box in first["results"]] == [1, 2]
        assert [box["number"] for box in rest["results"]] == [3]
        assert rest["next_cursor"] is None
        assert [box["number"] for box in packed["results"]] == [2]
        assert [box["number"] for box in by_number["results"]] == [3]

    async def test_a_malformed_cursor_is_rejected(self, move: Move) -> None:
        response = await move.call(
            "GET", f"/projects/{move.project_id}/boxes", params={"after": "not-a-cursor"}
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_pagination"

    async def test_update_is_partial(self, move: Move) -> None:
        box = await move.box(title="Книги")

        cleared = await move.ok("PATCH", f"/boxes/{box['id']}", json={"title": None})
        fragile = await move.ok("PATCH", f"/boxes/{box['id']}", json={"is_fragile": True})

        assert (cleared["title"], cleared["is_fragile"]) == (None, False)
        assert (fragile["title"], fragile["is_fragile"]) == (None, True)

    async def test_status_moves_one_step_and_is_logged(
        self, move: Move, session_maker: SessionMaker
    ) -> None:
        box = await move.box()
        path = f"/boxes/{box['id']}/status"

        packed = await move.ok("PUT", path, json={"status": "PACKED"})
        again = await move.ok("PUT", path, json={"status": "PACKED"})
        back = await move.ok("PUT", path, json={"status": "PACKING"})
        skip = await move.call("PUT", path, json={"status": "LOADED"})

        assert (packed["status"], again["status"], back["status"]) == (
            "PACKED",
            "PACKED",
            "PACKING",
        )
        assert skip.status_code == 409
        assert skip.json()["error"]["code"] == "invalid_box_transition"
        assert await audit_actions(session_maker, "BOX") == [
            "BOX_CREATED",
            "BOX_STATUS_CHANGED",
            "BOX_STATUS_ROLLED_BACK",
        ]

    async def test_delete_takes_the_items_along(self, move: Move) -> None:
        box = await move.box(
            items={"source_room_id": move.source_id, "entries": [{"name": "Книга"}]}
        )
        loose = await move.item("Лампа")

        await move.ok("DELETE", f"/boxes/{box['id']}", status=204)

        items = await move.ok("GET", f"/projects/{move.project_id}/items")
        gone = await move.call("GET", f"/boxes/{box['id']}")
        assert [item["id"] for item in items["results"]] == [loose["id"]]
        assert gone.status_code == 404


class TestContentsReminder:
    async def test_only_the_creator_sees_and_sets_it(self, move: Move) -> None:
        box = await move.box()
        remind_at = (datetime.now(UTC) + timedelta(hours=3)).replace(microsecond=0)
        path = f"/boxes/{box['id']}/contents-reminder"

        set_ = await move.ok("PUT", path, json={"remind_at": remind_at.isoformat()})
        as_member = await move.ok("GET", f"/boxes/{box['id']}", as_=move.member)
        by_member = await move.call(
            "PUT", path, json={"remind_at": remind_at.isoformat()}, as_=move.member
        )

        assert datetime.fromisoformat(set_["contents_reminder_at"]) == remind_at
        assert as_member["contents_reminder_at"] is None
        assert by_member.status_code == 403
        assert by_member.json()["error"]["code"] == "not_box_creator"

    async def test_packing_the_box_cancels_it(
        self, move: Move, session_maker: SessionMaker
    ) -> None:
        box = await move.box()
        remind_at = datetime.now(UTC) + timedelta(hours=3)
        await move.ok(
            "PUT",
            f"/boxes/{box['id']}/contents-reminder",
            json={"remind_at": remind_at.isoformat()},
        )

        packed = await move.ok("PUT", f"/boxes/{box['id']}/status", json={"status": "PACKED"})

        assert packed["contents_reminder_at"] is None
        async with session_maker() as session:
            statuses = list(await session.scalars(select(NotificationModel.status)))
        assert statuses == ["CANCELLED"]

    @pytest.mark.parametrize(
        ("status", "remind_in", "code"),
        [
            ("PACKED", timedelta(hours=1), "box_not_packing"),
            ("PACKING", -timedelta(hours=1), "reminder_in_the_past"),
        ],
    )
    async def test_rules(self, move: Move, status: str, remind_in: timedelta, code: str) -> None:
        box = await move.box()
        if status != "PACKING":
            await move.ok("PUT", f"/boxes/{box['id']}/status", json={"status": status})

        response = await move.call(
            "PUT",
            f"/boxes/{box['id']}/contents-reminder",
            json={"remind_at": (datetime.now(UTC) + remind_in).isoformat()},
        )

        assert response.json()["error"]["code"] == code


class TestQr:
    @pytest.mark.parametrize(
        ("strategy", "who", "status", "access"),
        [
            (QrCodeStrategy.PROTECTED, "anonymous", 401, None),
            (QrCodeStrategy.PROTECTED, "stranger", 404, None),
            (QrCodeStrategy.PROTECTED, "member", 200, "WRITE"),
            (QrCodeStrategy.OPEN, "anonymous", 200, "READ"),
            (QrCodeStrategy.OPEN, "stranger", 200, "READ"),
            (QrCodeStrategy.OPEN, "member", 200, "WRITE"),
        ],
    )
    async def test_access_matrix(
        self,
        client: AsyncClient,
        strategy: QrCodeStrategy,
        who: str,
        status: int,
        access: str | None,
    ) -> None:
        owner = await signed_up(client, username="owner")
        stranger = await signed_up(client, username="stranger")
        project = await create_project(client, owner, qr_code_strategy=strategy.value)
        project_id = project["id"]
        room = await client.post(
            f"{API}/projects/{project_id}/rooms",
            json={"kind": "DESTINATION", "name": "Зал"},
            headers=owner.headers,
        )
        box = await client.post(
            f"{API}/projects/{project_id}/boxes",
            json={"destination_room_id": room.json()["id"]},
            headers=owner.headers,
        )
        headers = {"anonymous": {}, "stranger": stranger.headers, "member": owner.headers}[who]

        response = await client.get(f"{API}/boxes/by-qr/{box.json()['qr_token']}", headers=headers)

        assert response.status_code == status, response.text
        if access is not None:
            body = response.json()
            assert body["access"] == access
            assert body["project"] == {"id": project_id, "name": project["name"]}
            assert body["box"]["number"] == 1

    async def test_unknown_or_malformed_tokens_are_not_found(self, move: Move) -> None:
        unknown = await move.call("GET", "/boxes/by-qr/AAAAAAAAAAAAAAAAAAAAAA")
        malformed = await move.call("GET", "/boxes/by-qr/short")

        assert (unknown.status_code, malformed.status_code) == (404, 404)


class TestItems:
    async def test_create_in_room_and_in_box(self, move: Move) -> None:
        box = await move.box()

        loose = await move.item("Лампа")
        packed = await move.item("Кружка", box_id=box["id"], priority="HIGH", quantity=2)

        assert (loose["state"], loose["box"], loose["priority"]) == ("TO_PACK", None, "MEDIUM")
        assert loose["source_room"] == {"id": move.source_id, "name": "Кухня"}
        assert packed["state"] == "IN_BOX"
        assert packed["box"]["number"] == 1
        assert (packed["priority"], packed["quantity"]) == ("HIGH", 2)

    async def test_batch_is_all_or_nothing(self, move: Move) -> None:
        response = await move.call(
            "POST",
            f"/projects/{move.project_id}/items/batch",
            json={"source_room_id": move.source_id, "entries": [{"name": "Книга"}, {"name": " "}]},
        )
        created = await move.ok(
            "POST",
            f"/projects/{move.project_id}/items/batch",
            json={
                "source_room_id": move.source_id,
                "entries": [{"name": "Чашка"}, {"name": "Блюдо"}],
            },
            status=201,
        )

        assert response.json()["error"]["code"] == "invalid_item_name"
        assert [item["name"] for item in created] == ["Чашка", "Блюдо"]  # in the given order
        listed = await move.ok("GET", f"/projects/{move.project_id}/items")
        assert len(listed["results"]) == 2

    async def test_list_in_packing_order_with_cursor(self, move: Move) -> None:
        for name, priority in [
            ("Ваза", "LOW"),
            ("аптечка", "FIRST_NECESSITY"),
            ("Бокал", "LOW"),
            ("Плед", "HIGH"),
        ]:
            await move.item(name, priority=priority)
        path = f"/projects/{move.project_id}/items"

        first = await move.ok("GET", path, params={"limit": 3})
        rest = await move.ok("GET", path, params={"limit": 3, "after": first["next_cursor"]})
        low = await move.ok("GET", path, params={"priority": "LOW"})

        assert [item["name"] for item in first["results"]] == ["аптечка", "Плед", "Бокал"]
        assert [item["name"] for item in rest["results"]] == ["Ваза"]
        assert [item["name"] for item in low["results"]] == ["Бокал", "Ваза"]

    async def test_lose_get_suggestions_and_find(
        self, move: Move, session_maker: SessionMaker
    ) -> None:
        box = await move.box()
        await move.item("Кружка", box_id=box["id"])
        await move.item("Тарелка", box_id=box["id"])
        lamp = await move.item("Лампа")
        path = f"/items/{lamp['id']}/state"

        lost = await move.ok("PUT", path, json={"state": "LOST"})
        suggestions = await move.ok("GET", f"/items/{lamp['id']}/suggestions")
        found = await move.ok("PUT", path, json={"state": "IN_BOX", "box_id": box["id"]})

        assert (lost["state"], lost["box"]) == ("LOST", None)
        assert suggestions == [
            {
                "box": {
                    "id": box["id"],
                    "number": 1,
                    "status": "PACKING",
                    "destination_room": {"id": move.destination_id, "name": "Кухня"},
                },
                "matches": 2,
            }
        ]
        assert (found["state"], found["box"]["id"]) == ("IN_BOX", box["id"])
        assert (await audit_actions(session_maker, "ITEM")) == ["ITEM_MARKED_LOST", "ITEM_RETURNED"]

    @pytest.mark.parametrize(
        ("body", "status", "code"),
        [
            ({"state": "IN_BOX"}, 422, "item_box_mismatch"),
            ({"state": "LOST", "box_id": str(new_id())}, 422, "item_box_mismatch"),
            ({"state": "TAKEN_OUT"}, 409, "invalid_item_transition"),
            ({"state": "IN_BOX", "box_id": str(new_id())}, 404, "box_not_found"),
        ],
    )
    async def test_state_rules(
        self, move: Move, body: dict[str, str], status: int, code: str
    ) -> None:
        item = await move.item()

        response = await move.call("PUT", f"/items/{item['id']}/state", json=body)

        assert (response.status_code, response.json()["error"]["code"]) == (status, code)

    async def test_repeating_a_state_changes_nothing(self, move: Move) -> None:
        item = await move.item()

        first = await move.ok("PUT", f"/items/{item['id']}/state", json={"state": "LOST"})
        second = await move.ok("PUT", f"/items/{item['id']}/state", json={"state": "LOST"})

        assert first == second

    async def test_update_and_delete(self, move: Move) -> None:
        item = await move.item("Чайник")

        updated = await move.ok(
            "PATCH", f"/items/{item['id']}", json={"name": "Электрочайник", "priority": "HIGH"}
        )
        await move.ok("DELETE", f"/items/{item['id']}", status=204)

        assert (updated["name"], updated["priority"], updated["quantity"]) == (
            "Электрочайник",
            "HIGH",
            1,
        )
        assert (await move.call("GET", f"/items/{item['id']}")).status_code == 404

    async def test_search_by_word_prefix(self, move: Move) -> None:
        charger = await move.item("Зарядка для телефона")
        await move.item("Лампа")
        path = f"/projects/{move.project_id}/items/search"

        by_prefix = await move.ok("GET", path, params={"q": "зарядк"})
        by_form = await move.ok("GET", path, params={"q": "зарядки телефон"})
        nothing = await move.ok("GET", path, params={"q": "  !!! "})

        assert [item["id"] for item in by_prefix["results"]] == [charger["id"]]
        assert [item["id"] for item in by_form["results"]] == [charger["id"]]
        assert nothing == {"results": [], "has_more": False}

    async def test_members_share_the_inventory_and_strangers_do_not(self, move: Move) -> None:
        item = await move.item()

        as_member = await move.call("GET", f"/items/{item['id']}", as_=move.member)
        as_stranger = await move.call("GET", f"/items/{item['id']}", as_=move.stranger)

        assert as_member.status_code == 200
        assert (as_stranger.status_code, as_stranger.json()["error"]["code"]) == (
            404,
            "item_not_found",
        )
