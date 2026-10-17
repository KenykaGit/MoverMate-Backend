"""Active room names are unique per project and kind; the error leaves the transaction usable."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.exceptions import RoomNameTakenError
from app.domain.inventory.room import Room
from app.domain.inventory.value_objects import RoomKind
from app.domain.projects.entity import Project
from app.infrastructure.db.models import RoomModel
from app.infrastructure.db.repositories.project import SqlAlchemyProjectRepository
from app.infrastructure.db.repositories.room import SqlAlchemyRoomRepository
from app.infrastructure.db.repositories.user import SqlAlchemyUserRepository
from tests.factories import make_project, make_room, make_user

type SessionMaker = async_sessionmaker[AsyncSession]


@pytest.fixture
async def project(session_maker: SessionMaker) -> Project:
    owner = make_user()
    project = make_project(owner_id=owner.id)
    async with session_maker() as session:
        await SqlAlchemyUserRepository(session).add(owner)
        await SqlAlchemyProjectRepository(session).add(project)
        await session.commit()
    return project


async def stored_rooms(session_maker: SessionMaker) -> list[tuple[str, str, bool]]:
    async with session_maker() as session:
        rows = await session.execute(select(RoomModel.kind, RoomModel.name, RoomModel.is_active))
        return sorted((kind, name, is_active) for kind, name, is_active in rows)


async def test_taken_name_raises_and_keeps_the_transaction_usable(
    session_maker: SessionMaker, project: Project
) -> None:
    async with session_maker() as session:
        rooms = SqlAlchemyRoomRepository(session)
        await rooms.add(make_room(project_id=project.id, name="Kitchen"))

        with pytest.raises(RoomNameTakenError) as exc_info:
            # The same name after normalization: trimmed.
            await rooms.add(make_room(project_id=project.id, name="  Kitchen "))
        await rooms.add(make_room(project_id=project.id, name="Hall"))
        await session.commit()

    assert (exc_info.value.kind, str(exc_info.value.name)) == (RoomKind.SOURCE, "Kitchen")
    assert await stored_rooms(session_maker) == [
        ("SOURCE", "Hall", True),
        ("SOURCE", "Kitchen", True),
    ]


async def test_same_name_is_free_elsewhere(session_maker: SessionMaker, project: Project) -> None:
    other_owner = make_user(username="other")
    other_project = make_project(owner_id=other_owner.id)
    deleted = make_room(project_id=project.id, name="Kitchen")
    deleted.soft_delete()
    candidates: list[Room] = [
        deleted,
        make_room(project_id=project.id, name="Kitchen"),  # the deleted one does not clash
        make_room(project_id=project.id, kind=RoomKind.DESTINATION, name="Kitchen"),
        make_room(project_id=other_project.id, name="Kitchen"),
    ]

    async with session_maker() as session:
        await SqlAlchemyUserRepository(session).add(other_owner)
        await SqlAlchemyProjectRepository(session).add(other_project)
        rooms = SqlAlchemyRoomRepository(session)
        for room in candidates:
            await rooms.add(room)
        await session.commit()

    assert await stored_rooms(session_maker) == [
        ("DESTINATION", "Kitchen", True),
        ("SOURCE", "Kitchen", False),
        ("SOURCE", "Kitchen", True),
        ("SOURCE", "Kitchen", True),
    ]
