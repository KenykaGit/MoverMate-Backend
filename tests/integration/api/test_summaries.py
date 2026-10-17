"""Overview screens end to end: access, the JSON contract, boundaries.

What is counted and in which order is covered against the read model itself
(tests/integration/repositories/test_summary_reader.py); here the HTTP layer.
"""

from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response

from app.domain.common.ids import ProjectId, UserId
from app.domain.inventory.box import Box, BoxStatus
from app.domain.inventory.item import Item
from app.domain.inventory.room import Room
from app.domain.inventory.value_objects import BoxNumber, BoxTitle, ItemName, Priority
from app.domain.tasks.entity import Task
from app.domain.tasks.value_objects import TaskPhase, TaskTitle
from app.presentation.http.routes.summaries import MAX_LABELS_PER_REQUEST
from tests.factories import NOW
from tests.integration.helpers import API, Session, add_collaborator, create_project, signed_up
from tests.integration.seed import SessionMaker, destination, rooms_of, source, store

SUFFIXES = ["/summary", "/loading-checklist", "/unpacking-progress", "/labels"]


def url(project_id: str, suffix: str) -> str:
    return f"{API}/projects/{project_id}{suffix}"


def error_code(response: Response) -> str:
    code: str = response.json()["error"]["code"]
    return code


@pytest.fixture
async def owner(client: AsyncClient) -> Session:
    return await signed_up(client, username="owner")


@pytest.fixture
async def member(client: AsyncClient) -> Session:
    return await signed_up(client, username="member")


@pytest.fixture
async def project(client: AsyncClient, owner: Session, member: Session) -> dict[str, Any]:
    created = await create_project(
        client,
        owner,
        rooms={"source": ["Kitchen", "Hall"], "destination": ["Bedroom", "Study"]},
    )
    await add_collaborator(client, created["id"], owner, "member")
    return created


@pytest.fixture
async def rooms(session_maker: SessionMaker, project: dict[str, Any]) -> dict[str, Room]:
    return await rooms_of(session_maker, project["id"])


class Seeder:
    """Boxes, items and tasks of the test project (they have no endpoints yet)."""

    def __init__(self, session_maker: SessionMaker, rooms: dict[str, Room], owner: Session):
        self.session_maker = session_maker
        self.rooms = rooms
        self.project_id = ProjectId(next(iter(rooms.values())).project_id)
        self.owner_id = UserId(UUID(owner.user_id))

    def box(self, number: int, status: BoxStatus, to: str = "Bedroom", **kw: Any) -> Box:
        box = Box.create(
            project_id=self.project_id,
            number=BoxNumber(number),
            destination=destination(self.rooms, to),
            created_by=self.owner_id,
            now=NOW,
            **kw,
        )
        for step in list(BoxStatus)[1 : status.position + 1]:
            box.change_status(step, now=NOW)
        return box

    def item(self, name: str, into: Box | None = None, **kw: Any) -> Item:
        item = Item.create(
            project_id=self.project_id,
            source=source(self.rooms, "Kitchen"),
            name=ItemName(name),
            **kw,
        )
        if into is not None:
            item.put_into(into)
        return item

    async def store(self, *entities: Box | Item | Task) -> None:
        await store(self.session_maker, *entities)


@pytest.fixture
def seed(session_maker: SessionMaker, rooms: dict[str, Room], owner: Session) -> Seeder:
    return Seeder(session_maker, rooms, owner)


class TestAccess:
    @pytest.mark.parametrize("suffix", SUFFIXES)
    async def test_members_read(
        self, client: AsyncClient, member: Session, project: dict[str, Any], suffix: str
    ) -> None:
        response = await client.get(url(project["id"], suffix), headers=member.headers)

        assert response.status_code == 200, response.text

    @pytest.mark.parametrize("suffix", SUFFIXES)
    async def test_strangers_get_the_404_of_a_missing_project(
        self, client: AsyncClient, project: dict[str, Any], suffix: str
    ) -> None:
        stranger = await signed_up(client, username="stranger")

        foreign = await client.get(url(project["id"], suffix), headers=stranger.headers)
        missing = await client.get(url(str(uuid4()), suffix), headers=stranger.headers)

        assert (foreign.status_code, error_code(foreign)) == (404, "project_not_found")
        assert foreign.json()["error"]["message"] != ""
        assert (missing.status_code, error_code(missing)) == (404, "project_not_found")

    @pytest.mark.parametrize("suffix", SUFFIXES)
    async def test_login_is_required(
        self, client: AsyncClient, project: dict[str, Any], suffix: str
    ) -> None:
        response = await client.get(url(project["id"], suffix))

        assert (response.status_code, error_code(response)) == (401, "not_authenticated")

    @pytest.mark.parametrize("suffix", SUFFIXES)
    async def test_project_id_must_be_a_uuid(
        self, client: AsyncClient, owner: Session, suffix: str
    ) -> None:
        response = await client.get(url("42", suffix), headers=owner.headers)

        assert (response.status_code, error_code(response)) == (422, "validation_error")
        assert response.json()["error"]["details"][0]["field"] == "path.project_id"


class TestSummary:
    async def test_new_project_has_every_counter_at_zero(
        self, client: AsyncClient, owner: Session, project: dict[str, Any]
    ) -> None:
        response = await client.get(url(project["id"], "/summary"), headers=owner.headers)

        zero = {"total": 0, "done": 0}
        assert response.json() == {
            "boxes": {
                "total": 0,
                "by_status": dict.fromkeys(
                    ["PACKING", "PACKED", "LOADED", "DELIVERED", "UNPACKED"], 0
                ),
            },
            "tasks": {
                "total": 0,
                "done": 0,
                "by_phase": {"BEFORE": zero, "DURING": zero, "AFTER": zero},
            },
            "items": {
                "total": 0,
                "by_state": dict.fromkeys(["TO_PACK", "IN_BOX", "TAKEN_OUT", "LOST"], 0),
            },
            "first_necessity": {"total": 0, "taken_out": 0},
            "next_task": None,
        }

    async def test_counters_and_the_next_task(
        self,
        client: AsyncClient,
        owner: Session,
        member: Session,
        project: dict[str, Any],
        seed: Seeder,
    ) -> None:
        loaded = seed.box(1, BoxStatus.LOADED)
        lamp = seed.item("Lamp", into=loaded, priority=Priority.FIRST_NECESSITY)
        lamp.take_out()
        next_task = Task.create(
            project_id=seed.project_id,
            title=TaskTitle("Book a truck"),
            phase=TaskPhase.BEFORE,
            due_at=NOW,
        )
        next_task.assign(UserId(UUID(member.user_id)))
        done = Task.create(project_id=seed.project_id, title=TaskTitle("Find movers"))
        done.complete(now=NOW)
        await seed.store(
            loaded,
            seed.box(2, BoxStatus.PACKING),
            lamp,
            seed.item("Kettle", priority=Priority.FIRST_NECESSITY),
            next_task,
            done,
        )

        body = (await client.get(url(project["id"], "/summary"), headers=owner.headers)).json()

        assert (body["boxes"]["total"], body["boxes"]["by_status"]["LOADED"]) == (2, 1)
        assert (body["items"]["total"], body["items"]["by_state"]["TAKEN_OUT"]) == (2, 1)
        assert body["first_necessity"] == {"total": 2, "taken_out": 1}
        assert (body["tasks"]["total"], body["tasks"]["done"]) == (2, 1)
        assert body["tasks"]["by_phase"]["BEFORE"] == {"total": 2, "done": 1}
        task = body["next_task"]
        assert {key: task[key] for key in task if key not in {"created_at", "updated_at"}} == {
            "id": str(next_task.id),
            "title": "Book a truck",
            "description": None,
            "phase": "BEFORE",
            "due_at": NOW.isoformat().replace("+00:00", "Z"),
            "is_done": False,
            "done_at": None,
            "assignees": [{"id": member.user_id, "username": "member"}],
            "reminder_offsets_hours": [],
        }


class TestRoomProgress:
    async def test_loading_checklist(
        self, client: AsyncClient, owner: Session, project: dict[str, Any], seed: Seeder
    ) -> None:
        packed = seed.box(4, BoxStatus.PACKED, to="Study")
        await seed.store(packed, seed.item("Cups", into=packed), seed.item("Kettle"))

        response = await client.get(url(project["id"], "/loading-checklist"), headers=owner.headers)

        assert response.status_code == 200
        [hall, kitchen] = response.json()
        assert (hall["room"]["name"], hall["to_pack_count"], hall["boxes"], hall["is_loaded"]) == (
            "Hall",
            0,
            [],
            True,
        )
        assert kitchen["room"]["id"] == str(source(seed.rooms, "Kitchen").id)
        assert (kitchen["to_pack_count"], kitchen["is_loaded"]) == (1, False)
        assert kitchen["boxes"] == [
            {
                "id": str(packed.id),
                "number": 4,
                "status": "PACKED",
                "destination_room": {
                    "id": str(destination(seed.rooms, "Study").id),
                    "name": "Study",
                },
            }
        ]

    async def test_unpacking_progress(
        self, client: AsyncClient, owner: Session, project: dict[str, Any], seed: Seeder
    ) -> None:
        await seed.store(seed.box(1, BoxStatus.UNPACKED), seed.box(2, BoxStatus.DELIVERED))

        response = await client.get(
            url(project["id"], "/unpacking-progress"), headers=owner.headers
        )

        assert response.json() == [
            {
                "room": {"id": str(destination(seed.rooms, "Bedroom").id), "name": "Bedroom"},
                "total": 2,
                "unpacked": 1,
            },
            {
                "room": {"id": str(destination(seed.rooms, "Study").id), "name": "Study"},
                "total": 0,
                "unpacked": 0,
            },
        ]


class TestLabels:
    @pytest.fixture
    async def boxes(self, seed: Seeder) -> list[Box]:
        boxes = [
            seed.box(1, BoxStatus.PACKED, title=BoxTitle("Dishes"), is_fragile=True),
            seed.box(2, BoxStatus.PACKING),
            seed.box(3, BoxStatus.PACKING, to="Study"),
        ]
        await seed.store(
            *boxes, seed.item("Cups", into=boxes[0]), seed.item("Plates", into=boxes[0])
        )
        return boxes

    async def test_every_box_by_number(
        self, client: AsyncClient, owner: Session, project: dict[str, Any], boxes: list[Box]
    ) -> None:
        response = await client.get(url(project["id"], "/labels"), headers=owner.headers)

        labels = response.json()
        assert [label["number"] for label in labels] == [1, 2, 3]
        assert labels[0] == {
            "id": str(boxes[0].id),
            "number": 1,
            "title": "Dishes",
            "is_fragile": True,
            "destination_room": {"id": labels[0]["destination_room"]["id"], "name": "Bedroom"},
            "qr_token": boxes[0].qr_token.value,
            "item_names": ["Cups", "Plates"],
        }
        assert labels[1]["item_names"] == []

    async def test_chosen_boxes_by_repeating_the_parameter(
        self, client: AsyncClient, owner: Session, project: dict[str, Any], boxes: list[Box]
    ) -> None:
        response = await client.get(
            url(project["id"], "/labels"),
            params=[
                ("box_ids", str(boxes[2].id)),
                ("box_ids", str(boxes[0].id)),
                ("box_ids", str(uuid4())),
            ],
            headers=owner.headers,
        )

        assert response.status_code == 200
        assert [label["number"] for label in response.json()] == [1, 3]

    async def test_at_most_a_page_of_chosen_boxes(
        self, client: AsyncClient, owner: Session, project: dict[str, Any]
    ) -> None:
        too_many = {"box_ids": [str(uuid4()) for _ in range(MAX_LABELS_PER_REQUEST + 1)]}

        response = await client.get(
            url(project["id"], "/labels"), params=too_many, headers=owner.headers
        )

        assert (response.status_code, error_code(response)) == (422, "validation_error")
        assert response.json()["error"]["details"][0]["field"] == "query.box_ids"

    async def test_chosen_ids_must_be_uuids(
        self, client: AsyncClient, owner: Session, project: dict[str, Any]
    ) -> None:
        response = await client.get(
            url(project["id"], "/labels"), params={"box_ids": "7"}, headers=owner.headers
        )

        assert (response.status_code, error_code(response)) == (422, "validation_error")
