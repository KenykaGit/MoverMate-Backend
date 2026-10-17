"""The overview read model against real rows: what is counted, what is skipped, order."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.common.ids import BoxId, new_id
from app.domain.common.reminders import ReminderOffset
from app.domain.inventory.box import Box, BoxStatus
from app.domain.inventory.item import Item, ItemState
from app.domain.inventory.room import Room
from app.domain.inventory.value_objects import (
    BoxNumber,
    BoxTitle,
    ItemName,
    Priority,
    RoomKind,
    RoomName,
)
from app.domain.projects.entity import Project
from app.domain.tasks.entity import Task
from app.domain.tasks.value_objects import TaskPhase, TaskTitle
from app.domain.users.entity import User
from app.infrastructure.db.queries.summaries import SqlAlchemySummaryReadModel
from tests.factories import NOW, make_project, make_user
from tests.integration.seed import SessionMaker, store


@dataclass
class World:
    owner: User
    member: User
    project: Project
    kitchen: Room
    hall: Room
    bedroom: Room
    study: Room


def room(project: Project, kind: RoomKind, name: str) -> Room:
    return Room.create(project_id=project.id, kind=kind, name=RoomName(name))


def box(
    world: World,
    number: int,
    status: BoxStatus = BoxStatus.PACKING,
    *,
    destination: Room | None = None,
    title: str | None = None,
    is_fragile: bool = False,
) -> Box:
    """A box of `world.project` at `status`, headed for the bedroom by default."""
    created = Box.create(
        project_id=world.project.id,
        number=BoxNumber(number),
        destination=destination or world.bedroom,
        created_by=world.owner.id,
        now=NOW,
        title=None if title is None else BoxTitle(title),
        is_fragile=is_fragile,
    )
    for step in list(BoxStatus)[1 : status.position + 1]:
        created.change_status(step, now=NOW)
    return created


def item(
    world: World,
    name: str,
    *,
    source: Room | None = None,
    into: Box | None = None,
    priority: Priority = Priority.MEDIUM,
) -> Item:
    created = Item.create(
        project_id=world.project.id,
        source=source or world.kitchen,
        name=ItemName(name),
        priority=priority,
    )
    if into is not None:
        created.put_into(into)
    return created


def task(world: World, title: str, phase: TaskPhase, due_in_days: int | None = None) -> Task:
    return Task.create(
        project_id=world.project.id,
        title=TaskTitle(title),
        phase=phase,
        due_at=None if due_in_days is None else NOW + timedelta(days=due_in_days),
    )


@pytest.fixture
async def world(session_maker: SessionMaker) -> World:
    owner, member = make_user(username="owner"), make_user(username="member")
    project = make_project(owner_id=owner.id)
    project.add_member(member.id)
    world = World(
        owner=owner,
        member=member,
        project=project,
        kitchen=room(project, RoomKind.SOURCE, "Kitchen"),
        hall=room(project, RoomKind.SOURCE, "Hall"),
        bedroom=room(project, RoomKind.DESTINATION, "Bedroom"),
        study=room(project, RoomKind.DESTINATION, "Study"),
    )
    await store(
        session_maker,
        owner,
        member,
        project,
        world.kitchen,
        world.hall,
        world.bedroom,
        world.study,
    )
    return world


@pytest.fixture
async def stranger_world(session_maker: SessionMaker) -> World:
    """Another project full of things: none of them may leak into `world`'s answers."""
    owner = make_user(username="stranger")
    project = make_project(owner_id=owner.id)
    other = World(
        owner=owner,
        member=owner,
        project=project,
        kitchen=room(project, RoomKind.SOURCE, "Kitchen"),
        hall=room(project, RoomKind.SOURCE, "Garage"),
        bedroom=room(project, RoomKind.DESTINATION, "Bedroom"),
        study=room(project, RoomKind.DESTINATION, "Office"),
    )
    foreign = box(other, 1, BoxStatus.UNPACKED)
    await store(
        session_maker,
        owner,
        project,
        other.kitchen,
        other.hall,
        other.bedroom,
        other.study,
        foreign,
        item(other, "Foreign", into=foreign, priority=Priority.FIRST_NECESSITY),
        item(other, "Foreign too"),
        task(other, "Foreign task", TaskPhase.BEFORE),
    )
    return other


@pytest.fixture
def reader(session: AsyncSession) -> SqlAlchemySummaryReadModel:
    """Reads in its own session: it sees what store() committed, statement by statement."""
    return SqlAlchemySummaryReadModel(session)


@pytest.mark.usefixtures("stranger_world")
class TestProjectSummary:
    async def test_empty_project_has_every_counter_at_zero(
        self,
        reader: SqlAlchemySummaryReadModel,
        world: World,
    ) -> None:
        summary = await reader.project_summary(world.project.id)

        assert summary.boxes_by_status == dict.fromkeys(BoxStatus, 0)
        assert summary.items_by_state == dict.fromkeys(ItemState, 0)
        assert {phase: (c.total, c.done) for phase, c in summary.tasks_by_phase.items()} == (
            dict.fromkeys(TaskPhase, (0, 0))
        )
        assert (summary.first_necessity.total, summary.first_necessity.done) == (0, 0)
        assert summary.next_task is None

    async def test_counts_active_boxes_items_and_tasks_of_the_project(
        self,
        session_maker: SessionMaker,
        reader: SqlAlchemySummaryReadModel,
        world: World,
    ) -> None:
        packing, packed = box(world, 1), box(world, 2, BoxStatus.PACKED)
        loaded = box(world, 3, BoxStatus.LOADED)
        deleted_box = box(world, 4, BoxStatus.LOADED)
        deleted_box.soft_delete()
        taken_out = item(world, "Lamp", into=packed, priority=Priority.FIRST_NECESSITY)
        taken_out.take_out()
        lost = item(world, "Charger", into=packed)
        lost.mark_lost()
        deleted_item = item(world, "Old", priority=Priority.FIRST_NECESSITY)
        deleted_item.soft_delete()
        done_before = task(world, "Order movers", TaskPhase.BEFORE)
        done_before.complete(now=NOW)
        done_after = task(world, "Hang curtains", TaskPhase.AFTER)
        done_after.complete(now=NOW)
        deleted_task = task(world, "Gone", TaskPhase.DURING)
        deleted_task.soft_delete()
        await store(
            session_maker,
            packing,
            packed,
            loaded,
            deleted_box,
            item(world, "Kettle", priority=Priority.FIRST_NECESSITY),
            item(world, "Cups", into=packing),
            taken_out,
            lost,
            deleted_item,
            done_before,
            task(world, "Pack books", TaskPhase.BEFORE),
            done_after,
            deleted_task,
        )

        summary = await reader.project_summary(world.project.id)

        assert summary.boxes_by_status == {
            BoxStatus.PACKING: 1,
            BoxStatus.PACKED: 1,
            BoxStatus.LOADED: 1,
            BoxStatus.DELIVERED: 0,
            BoxStatus.UNPACKED: 0,
        }
        assert summary.items_by_state == dict.fromkeys(ItemState, 1)
        assert {phase: (c.total, c.done) for phase, c in summary.tasks_by_phase.items()} == {
            TaskPhase.BEFORE: (2, 1),
            TaskPhase.DURING: (0, 0),
            TaskPhase.AFTER: (1, 1),
        }
        assert (summary.first_necessity.total, summary.first_necessity.done) == (2, 1)
        assert (summary.boxes_total, summary.items_total) == (3, 4)

    async def test_next_task_goes_by_phase_then_due_time_then_creation(
        self,
        session_maker: SessionMaker,
        reader: SqlAlchemySummaryReadModel,
        world: World,
    ) -> None:
        after_due_soon = task(world, "Unpack", TaskPhase.AFTER, due_in_days=1)
        during = task(world, "Meet movers", TaskPhase.DURING)
        before_no_due = task(world, "Sort clothes", TaskPhase.BEFORE)
        before_no_due_later = task(world, "Sort books", TaskPhase.BEFORE)
        before_due = task(world, "Book a truck", TaskPhase.BEFORE, due_in_days=10)
        before_done = task(world, "Find movers", TaskPhase.BEFORE, due_in_days=1)
        before_done.complete(now=NOW)
        before_deleted = task(world, "Deleted", TaskPhase.BEFORE, due_in_days=1)
        before_deleted.soft_delete()
        await store(
            session_maker,
            after_due_soon,
            during,
            before_no_due,
            before_no_due_later,
            before_due,
            before_done,
            before_deleted,
        )

        open_tasks: dict[UUID, Task] = {
            t.id: t
            for t in (after_due_soon, during, before_no_due, before_no_due_later, before_due)
        }
        order = []
        while (summary := await reader.project_summary(world.project.id)).next_task:
            order.append(summary.next_task.title)
            done = open_tasks[summary.next_task.id]
            done.complete(now=NOW)
            await store(session_maker, done)

        assert order == ["Book a truck", "Sort clothes", "Sort books", "Meet movers", "Unpack"]

    async def test_next_task_carries_active_assignees_and_reminders(
        self, session_maker: SessionMaker, reader: SqlAlchemySummaryReadModel, world: World
    ) -> None:
        ghost = make_user(username="ghost", is_active=False)
        world.project.add_member(ghost.id)
        await store(session_maker, ghost, world.project)
        next_task = task(world, "Book a truck", TaskPhase.BEFORE, due_in_days=3)
        for user in (world.owner, ghost, world.member):
            next_task.assign(user.id)
        next_task.add_reminder(ReminderOffset(24))
        next_task.add_reminder(ReminderOffset(2))
        await store(session_maker, next_task)

        found = (await reader.project_summary(world.project.id)).next_task

        assert found is not None
        assert (found.id, found.phase, found.is_done, found.done_at) == (
            next_task.id,
            TaskPhase.BEFORE,
            False,
            None,
        )
        assert found.due_at == NOW + timedelta(days=3)
        assert [user.username for user in found.assignees] == ["member", "owner"]
        assert found.reminder_offsets_hours == (2, 24)
        assert found.created_at is not None
        assert found.updated_at is not None


@pytest.mark.usefixtures("stranger_world")
class TestLoadingChecklist:
    async def test_rooms_of_the_old_home_with_their_boxes(
        self,
        session_maker: SessionMaker,
        reader: SqlAlchemySummaryReadModel,
        world: World,
    ) -> None:
        attic = room(world.project, RoomKind.SOURCE, "Attic")
        attic.soft_delete()
        bathroom = room(world.project, RoomKind.SOURCE, "Bathroom")
        one = box(world, 1, BoxStatus.LOADED)
        two = box(world, 2, BoxStatus.PACKED, destination=world.study)
        three = box(world, 3, BoxStatus.LOADED)
        deleted = box(world, 4)
        deleted.soft_delete()
        kettle = item(world, "Kettle")
        taken_out = item(world, "Spoon", into=one)
        taken_out.take_out()
        lost = item(world, "Fork")
        lost.mark_lost()
        deleted_item = item(world, "Knife")
        deleted_item.soft_delete()
        await store(
            session_maker,
            attic,
            bathroom,
            one,
            two,
            three,
            deleted,
            item(world, "Cups", into=one),
            item(world, "Plates", into=one),
            item(world, "Pan", into=two),
            kettle,
            taken_out,
            lost,
            deleted_item,
            item(world, "Lamp", source=world.hall, into=three),
            item(world, "Coat", source=world.hall, into=one),
        )

        rooms = await reader.loading_checklist(world.project.id)

        assert [
            (r.room.name, r.to_pack_count, [b.number for b in r.boxes], r.is_loaded) for r in rooms
        ] == [
            ("Bathroom", 0, [], True),
            ("Hall", 0, [1, 3], True),
            ("Kitchen", 1, [1, 2], False),
        ]
        kitchen_two = rooms[2].boxes[1]
        assert (kitchen_two.id, kitchen_two.status, kitchen_two.destination_room.name) == (
            two.id,
            BoxStatus.PACKED,
            "Study",
        )

        kettle.put_into(one)
        await store(session_maker, kettle)
        kitchen = (await reader.loading_checklist(world.project.id))[2]
        assert (kitchen.to_pack_count, kitchen.is_loaded) == (0, False)  # box 2 is not loaded

        two.change_status(BoxStatus.LOADED, now=NOW)
        await store(session_maker, two)
        kitchen = (await reader.loading_checklist(world.project.id))[2]
        assert kitchen.is_loaded


@pytest.mark.usefixtures("stranger_world")
class TestUnpackingProgress:
    async def test_rooms_of_the_new_home_count_their_active_boxes(
        self,
        session_maker: SessionMaker,
        reader: SqlAlchemySummaryReadModel,
        world: World,
    ) -> None:
        deleted = box(world, 3, BoxStatus.UNPACKED)
        deleted.soft_delete()
        await store(
            session_maker,
            box(world, 1, BoxStatus.UNPACKED),
            box(world, 2, BoxStatus.DELIVERED),
            deleted,
        )

        rooms = await reader.unpacking_progress(world.project.id)

        assert [(r.room.name, r.total, r.unpacked) for r in rooms] == [
            ("Bedroom", 2, 1),
            ("Study", 0, 0),
        ]
        assert rooms[0].room.id == world.bedroom.id


@pytest.mark.usefixtures("stranger_world")
class TestBoxLabels:
    @pytest.fixture
    async def boxes(self, session_maker: SessionMaker, world: World) -> dict[str, Box]:
        dishes = box(world, 1, title="Dishes", is_fragile=True)
        empty = box(world, 2, destination=world.study)
        deleted = box(world, 3)
        deleted.soft_delete()
        broken = item(world, "Broken cup", into=dishes)
        broken.soft_delete()
        await store(
            session_maker,
            dishes,
            empty,
            deleted,
            item(world, "plates", into=dishes),
            item(world, "Cups", into=dishes),
            item(world, "Kettle", into=dishes, priority=Priority.FIRST_NECESSITY),
            broken,
            item(world, "Not packed"),
        )
        return {"dishes": dishes, "empty": empty, "deleted": deleted}

    async def test_every_active_box_by_number(
        self,
        reader: SqlAlchemySummaryReadModel,
        world: World,
        boxes: dict[str, Box],
    ) -> None:
        labels = await reader.box_labels(world.project.id)

        dishes, empty = boxes["dishes"], boxes["empty"]
        assert [
            (
                label.id,
                label.number,
                label.title,
                label.is_fragile,
                label.destination_room.name,
                label.qr_token,
                label.item_names,
            )
            for label in labels
        ] == [
            (
                dishes.id,
                1,
                "Dishes",
                True,
                "Bedroom",
                dishes.qr_token.value,
                ("Kettle", "Cups", "plates"),
            ),
            (empty.id, 2, None, False, "Study", empty.qr_token.value, ()),
        ]

    async def test_chosen_boxes_only_and_foreign_ids_are_skipped(
        self,
        reader: SqlAlchemySummaryReadModel,
        world: World,
        stranger_world: World,
        boxes: dict[str, Box],
    ) -> None:
        foreign = await reader.box_labels(stranger_world.project.id)
        chosen = {
            boxes["empty"].id,
            boxes["deleted"].id,
            BoxId(foreign[0].id),
            BoxId(new_id()),
        }

        labels = await reader.box_labels(world.project.id, chosen)

        assert [label.id for label in labels] == [boxes["empty"].id]
        assert await reader.box_labels(world.project.id, set()) == []
