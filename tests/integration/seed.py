"""Test data written straight to the database through the mappers.

Rooms, boxes, items and tasks have no repositories or endpoints yet; the read
models are tested against rows that the domain produced, as the future
repositories will write them.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.inventory.box import Box
from app.domain.inventory.item import Item
from app.domain.inventory.room import Room
from app.domain.inventory.value_objects import RoomKind
from app.domain.projects.entity import Project
from app.domain.tasks.entity import Task
from app.domain.users.entity import User
from app.infrastructure.db.base import Base
from app.infrastructure.db.mappers.inventory import BoxMapper, ItemMapper, RoomMapper
from app.infrastructure.db.mappers.project import ProjectMapper
from app.infrastructure.db.mappers.task import TaskMapper
from app.infrastructure.db.mappers.user import UserMapper
from app.infrastructure.db.models import RoomModel

type SessionMaker = async_sessionmaker[AsyncSession]
type Seedable = User | Project | Room | Box | Item | Task


def _model(entity: Seedable) -> Base:
    match entity:
        case User():
            return UserMapper.to_model(entity)
        case Project():
            return ProjectMapper.to_model(entity)
        case Room():
            return RoomMapper.to_model(entity)
        case Box():
            return BoxMapper.to_model(entity)
        case Item():
            return ItemMapper.to_model(entity)
        case Task():
            return TaskMapper.to_model(entity)


async def store(session_maker: SessionMaker, *entities: Seedable) -> None:
    """Insert or update, in the given order: parents before children."""
    async with session_maker() as session:
        for entity in entities:
            await session.merge(_model(entity))
            await session.flush()
        await session.commit()


async def rooms_of(session_maker: SessionMaker, project_id: UUID | str) -> dict[str, Room]:
    """Active rooms of a project by "SOURCE:name" / "DESTINATION:name"."""
    async with session_maker() as session:
        models = await session.scalars(
            select(RoomModel).where(RoomModel.project_id == UUID(str(project_id)))
        )
        rooms = [RoomMapper.to_domain(model) for model in models]
    return {f"{room.kind}:{room.name}": room for room in rooms if room.is_active}


def source(rooms: dict[str, Room], name: str) -> Room:
    return rooms[f"{RoomKind.SOURCE}:{name}"]


def destination(rooms: dict[str, Room], name: str) -> Room:
    return rooms[f"{RoomKind.DESTINATION}:{name}"]
