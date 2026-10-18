from dataclasses import dataclass

import pytest

from app.application.inventory.create_box import CreateBox
from app.application.inventory.create_items import CreateItems
from app.application.inventory.list_rooms import ListRooms
from app.application.projects.create_project import CreateProject
from app.application.projects.services import MovingReminderScheduler
from app.application.seed.catalog import TEST_PASSWORD, TEST_USERS, SeedProject
from app.application.seed.dto import SeedTestUsersDTO
from app.application.seed.seed_test_users import SeedTestUsers
from app.application.tasks.create_task import CreateTask
from app.application.tasks.services import TaskNotifier
from app.application.users.register_user import RegisterUser
from app.domain.inventory.item import ItemState
from app.domain.inventory.value_objects import RoomKind
from tests.fakes import (
    FakeInventoryReadModel,
    FakePasswordHasher,
    FakeTaskReadModel,
    FakeUnitOfWork,
    InMemoryAuditLogRepository,
    InMemoryBoxRepository,
    InMemoryItemRepository,
    InMemoryNotificationRepository,
    InMemoryProjectReader,
    InMemoryProjectRepository,
    InMemoryRoomRepository,
    InMemoryTaskRepository,
    InMemoryUserRepository,
)


@dataclass(slots=True)
class World:
    users: InMemoryUserRepository
    projects: InMemoryProjectRepository
    rooms: InMemoryRoomRepository
    boxes: InMemoryBoxRepository
    items: InMemoryItemRepository
    tasks: InMemoryTaskRepository
    seed: SeedTestUsers

    def owned_by(self, username: str) -> list[SeedProject]:
        """The projects of `username` as stored, as catalog entries by name."""
        user = next(u for u in self.users.users.values() if u.username.value == username)
        names = {p.name.value for p in self.projects.projects.values() if p.owner_id == user.id}
        spec = next(u for u in TEST_USERS if u.username == username)
        return [project for project in spec.projects if project.name in names]


@pytest.fixture
def world() -> World:
    users = InMemoryUserRepository()
    projects = InMemoryProjectRepository()
    rooms = InMemoryRoomRepository()
    boxes = InMemoryBoxRepository()
    items = InMemoryItemRepository()
    tasks = InMemoryTaskRepository(projects)
    notifications = InMemoryNotificationRepository()
    audit_log = InMemoryAuditLogRepository()
    uow = FakeUnitOfWork()
    inventory = FakeInventoryReadModel(users, rooms, boxes, items, notifications)
    seed = SeedTestUsers(
        RegisterUser(users, FakePasswordHasher(), uow),
        CreateProject(
            projects,
            rooms,
            audit_log,
            MovingReminderScheduler(notifications),
            InMemoryProjectReader(projects, users),
            uow,
        ),
        ListRooms(projects, rooms),
        CreateBox(projects, rooms, boxes, items, audit_log, inventory, uow),
        CreateItems(projects, rooms, boxes, items, inventory, uow),
        CreateTask(
            projects,
            tasks,
            TaskNotifier(notifications),
            audit_log,
            FakeTaskReadModel(tasks, users),
            uow,
        ),
    )
    return World(users, projects, rooms, boxes, items, tasks, seed)


def counts_of(world: World, username: str) -> tuple[int, int, int, int, int]:
    """(projects, rooms, boxes, items, tasks) stored for the user."""
    user = next(u for u in world.users.users.values() if u.username.value == username)
    project_ids = {p.id for p in world.projects.projects.values() if p.owner_id == user.id}
    return (
        len(project_ids),
        sum(r.project_id in project_ids for r in world.rooms.rooms.values()),
        sum(b.project_id in project_ids for b in world.boxes.boxes.values()),
        sum(i.project_id in project_ids for i in world.items.items.values()),
        sum(t.project_id in project_ids for t in world.tasks.tasks.values()),
    )


def expected_counts(username: str) -> tuple[int, int, int, int, int]:
    projects = next(u for u in TEST_USERS if u.username == username).projects
    return (
        len(projects),
        sum(len(p.source_rooms) + len(p.destination_rooms) for p in projects),
        sum(len(p.boxes) for p in projects),
        sum(len(b.items) for p in projects for b in p.boxes)
        + sum(len(u.items) for p in projects for u in p.unpacked),
        sum(len(p.tasks) for p in projects),
    )


async def test_registers_five_accounts_with_one_known_password(world: World) -> None:
    result = await world.seed(SeedTestUsersDTO())

    assert len(TEST_USERS) == 5
    assert result.created == tuple(u.username for u in TEST_USERS)
    assert result.skipped == ()
    assert {u.username.value for u in world.users.users.values()} == set(result.created)
    assert {u.password_hash for u in world.users.users.values()} == {
        FakePasswordHasher.hash_of(TEST_PASSWORD)
    }


async def test_two_accounts_are_empty(world: World) -> None:
    await world.seed(SeedTestUsersDTO())

    empty = [u.username for u in TEST_USERS if not u.projects]
    assert len(empty) == 2
    for username in empty:
        assert counts_of(world, username) == (0, 0, 0, 0, 0)


async def test_the_others_own_projects_of_different_sizes(world: World) -> None:
    await world.seed(SeedTestUsersDTO())

    filled = [u.username for u in TEST_USERS if u.projects]
    assert len(filled) == 3
    actual = {username: counts_of(world, username) for username in filled}
    assert actual == {username: expected_counts(username) for username in filled}
    for column in range(5):
        values = [counts[column] for counts in actual.values()]
        assert len(set(values)) == len(values), f"column {column} must differ: {values}"


async def test_boxed_items_are_in_boxes_and_the_rest_wait_to_be_packed(world: World) -> None:
    await world.seed(SeedTestUsersDTO())

    states = [item.state for item in world.items.items.values()]
    assert {ItemState.IN_BOX, ItemState.TO_PACK} <= set(states)
    assert all(
        (item.box_id is not None) == (item.state is ItemState.IN_BOX)
        for item in world.items.items.values()
    )


async def test_every_project_has_rooms_of_both_kinds(world: World) -> None:
    await world.seed(SeedTestUsersDTO())

    for project_id in world.projects.projects:
        kinds = {r.kind for r in world.rooms.rooms.values() if r.project_id == project_id}
        assert kinds == {RoomKind.SOURCE, RoomKind.DESTINATION}


async def test_a_second_run_skips_the_existing_accounts_and_adds_nothing(world: World) -> None:
    await world.seed(SeedTestUsersDTO())
    before = {u.username: counts_of(world, u.username) for u in TEST_USERS}

    result = await world.seed(SeedTestUsersDTO())

    assert result.created == ()
    assert result.skipped == tuple(u.username for u in TEST_USERS)
    assert {u.username: counts_of(world, u.username) for u in TEST_USERS} == before


async def test_a_missing_account_is_added_without_touching_the_others(world: World) -> None:
    await world.seed(SeedTestUsersDTO())
    gone = next(u for u in world.users.users.values() if u.username.value == "test_medium")
    gone.soft_delete()
    await world.users.update(gone)

    result = await world.seed(SeedTestUsersDTO())

    assert result.created == ("test_medium",)
    assert len(result.skipped) == 4
