"""Inventory repositories under concurrency: what only a real database can show."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.exceptions import BoxStatusConflictError, RoomNameTakenError
from app.domain.common.ids import UserId
from app.domain.inventory.box import Box, BoxStatus
from app.domain.inventory.errors import ItemStateTransitionError
from app.domain.inventory.item import Item
from app.domain.inventory.room import Room
from app.domain.inventory.value_objects import ItemName, RoomKind, RoomName
from app.domain.projects.entity import Project
from app.infrastructure.db.mappers.project import ProjectMapper
from app.infrastructure.db.mappers.user import UserMapper
from app.infrastructure.db.repositories.box import SqlAlchemyBoxRepository
from app.infrastructure.db.repositories.item import SqlAlchemyItemRepository
from app.infrastructure.db.repositories.project import SqlAlchemyProjectRepository
from app.infrastructure.db.repositories.room import SqlAlchemyRoomRepository
from tests.factories import NOW, make_project, make_user

type SessionMaker = async_sessionmaker[AsyncSession]


@pytest.fixture
async def project(session_maker: SessionMaker) -> Project:
    owner = make_user()
    project = make_project(owner_id=owner.id)
    async with session_maker() as session:
        session.add(UserMapper.to_model(owner))
        await session.flush()
        session.add(ProjectMapper.to_model(project))
        await session.commit()
    return project


@pytest.fixture
async def rooms(session_maker: SessionMaker, project: Project) -> tuple[Room, Room]:
    source = Room.create(project_id=project.id, kind=RoomKind.SOURCE, name=RoomName("Кухня"))
    destination = Room.create(
        project_id=project.id, kind=RoomKind.DESTINATION, name=RoomName("Кухня")
    )
    async with session_maker() as session:
        repository = SqlAlchemyRoomRepository(session)
        await repository.add(source)
        await repository.add(destination)
        await session.commit()
    return source, destination


async def create_box(session: AsyncSession, project: Project, destination: Room) -> Box:
    """What CreateBox does: lock the project, take the lowest free number, insert."""
    await SqlAlchemyProjectRepository(session).lock_for_box_numbering(project.id)
    boxes = SqlAlchemyBoxRepository(session)
    box = Box.create(
        project_id=project.id,
        number=await boxes.next_free_number(project.id),
        destination=destination,
        created_by=UserId(project.owner_id),
        now=NOW,
    )
    await boxes.add(box)
    return box


async def test_concurrent_boxes_get_different_numbers(
    session_maker: SessionMaker, project: Project, rooms: tuple[Room, Room]
) -> None:
    _, destination = rooms
    async with session_maker() as first, session_maker() as second:
        mine = await create_box(first, project, destination)
        # Blocks on the project row lock until the first transaction ends.
        theirs_task = asyncio.create_task(create_box(second, project, destination))
        await asyncio.sleep(0.1)
        assert not theirs_task.done()
        await first.commit()
        theirs = await theirs_task
        await second.commit()

    assert (mine.number.value, theirs.number.value) == (1, 2)


async def test_a_concurrent_status_change_is_a_conflict(
    session_maker: SessionMaker, project: Project, rooms: tuple[Room, Room]
) -> None:
    _, destination = rooms
    async with session_maker() as session:
        box = await create_box(session, project, destination)
        await session.commit()

    async with session_maker() as first, session_maker() as second:
        mine = await SqlAlchemyBoxRepository(first).get(box.id)
        theirs = await SqlAlchemyBoxRepository(second).get(box.id)
        assert mine is not None
        assert theirs is not None

        mine.change_status(BoxStatus.PACKED, now=NOW)
        await SqlAlchemyBoxRepository(first).change_status(mine, expected=BoxStatus.PACKING)
        await first.commit()

        theirs.change_status(BoxStatus.PACKED, now=NOW)
        with pytest.raises(BoxStatusConflictError):
            await SqlAlchemyBoxRepository(second).change_status(theirs, expected=BoxStatus.PACKING)


async def test_the_database_rejects_a_move_from_a_stale_state(
    session_maker: SessionMaker, project: Project, rooms: tuple[Room, Room]
) -> None:
    """The domain checked the move against the state it loaded; the trigger checks
    it against the stored one."""
    source, destination = rooms
    async with session_maker() as session:
        box = await create_box(session, project, destination)
        item = Item.create(project_id=project.id, source=source, name=ItemName("Кружка"))
        item.put_into(box)
        await SqlAlchemyItemRepository(session).add_many([item])
        await session.commit()

    async with session_maker() as first, session_maker() as second:
        mine = await SqlAlchemyItemRepository(first).get(item.id)
        theirs = await SqlAlchemyItemRepository(second).get(item.id)
        assert mine is not None
        assert theirs is not None

        mine.take_out()  # IN_BOX -> TAKEN_OUT
        await SqlAlchemyItemRepository(first).update(mine)
        await first.commit()

        theirs.return_to_room()  # IN_BOX -> TO_PACK, but stored is TAKEN_OUT now
        with pytest.raises(ItemStateTransitionError):
            await SqlAlchemyItemRepository(second).update(theirs)


async def test_room_names_clash_only_among_active_rooms_of_a_kind(
    session_maker: SessionMaker, project: Project, rooms: tuple[Room, Room]
) -> None:
    source, _ = rooms
    duplicate = Room.create(project_id=project.id, kind=RoomKind.SOURCE, name=RoomName("Кухня"))

    async with session_maker() as session:
        repository = SqlAlchemyRoomRepository(session)
        with pytest.raises(RoomNameTakenError):
            await repository.add(duplicate)
        source.soft_delete()
        await repository.update(source)
        await repository.add(duplicate)  # the session is still usable after the conflict
        await session.commit()

    async with session_maker() as session:
        names = [
            room.name.value for room in await SqlAlchemyRoomRepository(session).list(project.id)
        ]
    assert names == ["Кухня", "Кухня"]
