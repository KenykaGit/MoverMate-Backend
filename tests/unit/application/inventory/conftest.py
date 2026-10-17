"""A move to work on: a project of `owner` with `member`, a source and a destination
kitchen, and a `stranger` outside the project. `preset.make(UseCase)` wires a use
case to the in-memory ports by the names of its constructor parameters."""

import inspect
from dataclasses import dataclass
from typing import Any

import pytest

from app.domain.common.ids import UserId
from app.domain.inventory.room import Room
from app.domain.inventory.value_objects import RoomKind, RoomName
from app.domain.projects.entity import Project
from app.domain.users.entity import User
from tests.factories import make_project, make_user
from tests.fakes import (
    FakeInventoryReadModel,
    FakeItemRecognizer,
    FakeUnitOfWork,
    InMemoryAuditLogRepository,
    InMemoryBoxRepository,
    InMemoryItemRepository,
    InMemoryNotificationRepository,
    InMemoryProjectRepository,
    InMemoryRoomRepository,
    InMemoryUserRepository,
)


@dataclass(slots=True)
class Preset:
    owner: User
    member: User
    stranger: User
    project: Project
    source: Room
    destination: Room
    users: InMemoryUserRepository
    projects: InMemoryProjectRepository
    rooms: InMemoryRoomRepository
    boxes: InMemoryBoxRepository
    items: InMemoryItemRepository
    notifications: InMemoryNotificationRepository
    audit_log: InMemoryAuditLogRepository
    read_model: FakeInventoryReadModel
    recognizer: FakeItemRecognizer
    uow: FakeUnitOfWork

    def make[T](self, use_case: type[T]) -> T:
        parameters = inspect.signature(use_case).parameters
        ports: dict[str, Any] = {name: getattr(self, name) for name in parameters}
        return use_case(**ports)


@pytest.fixture
def preset() -> Preset:
    return new_preset()


def new_preset() -> Preset:
    """A fresh move: for table-driven tests that need clean state per case."""
    owner, member, stranger = (make_user(username=name) for name in ("owner", "member", "other"))
    project = make_project(owner_id=owner.id)
    project.add_member(member.id)
    source = Room.create(project_id=project.id, kind=RoomKind.SOURCE, name=RoomName("Кухня"))
    destination = Room.create(
        project_id=project.id, kind=RoomKind.DESTINATION, name=RoomName("Кухня")
    )
    users = InMemoryUserRepository([owner, member, stranger])
    rooms = InMemoryRoomRepository([source, destination])
    boxes = InMemoryBoxRepository()
    items = InMemoryItemRepository()
    notifications = InMemoryNotificationRepository()
    return Preset(
        owner=owner,
        member=member,
        stranger=stranger,
        project=project,
        source=source,
        destination=destination,
        users=users,
        projects=InMemoryProjectRepository([project]),
        rooms=rooms,
        boxes=boxes,
        items=items,
        notifications=notifications,
        audit_log=InMemoryAuditLogRepository(),
        read_model=FakeInventoryReadModel(users, rooms, boxes, items, notifications),
        recognizer=FakeItemRecognizer(),
        uow=FakeUnitOfWork(),
    )


def other_project(preset: Preset) -> Project:
    """A project of the stranger: objects moved into it belong to another project."""
    project = make_project(owner_id=UserId(preset.stranger.id))
    preset.projects.projects[project.id] = project
    return project
