"""Loading what the caller may touch.

To someone who is not a member, a project and everything in it do not exist: every
miss below is a 404 of the object that was asked for, never a 403. Helpers only
read; the calling use case owns the transaction.
"""

from collections.abc import Sequence
from uuid import UUID

from app.application.exceptions import (
    BoxNotFoundError,
    ItemNotFoundError,
    ProjectNotFoundError,
    RoomNotFoundError,
)
from app.application.interfaces.box_repository import AbstractBoxRepository
from app.application.interfaces.item_repository import AbstractItemRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.room_repository import AbstractRoomRepository
from app.application.inventory.dto import BoxDTO, ItemDTO
from app.application.inventory.read_model import AbstractInventoryReadModel
from app.domain.common.ids import BoxId, ItemId, ProjectId, RoomId, UserId
from app.domain.inventory.box import Box
from app.domain.inventory.item import Item
from app.domain.inventory.room import Room
from app.domain.projects.entity import Project


async def _membership(
    projects: AbstractProjectRepository, project_id: ProjectId, user_id: UUID
) -> Project | None:
    project = await projects.get_by_id(project_id)
    if project is None or not project.is_member(UserId(user_id)):
        return None
    return project


async def project_of_member(
    projects: AbstractProjectRepository, project_id: UUID, user_id: UUID
) -> Project:
    project = await _membership(projects, ProjectId(project_id), user_id)
    if project is None:
        raise ProjectNotFoundError(project_id)
    return project


async def room_of_member(
    rooms: AbstractRoomRepository,
    projects: AbstractProjectRepository,
    room_id: UUID,
    user_id: UUID,
) -> Room:
    room = await rooms.get(RoomId(room_id))
    if room is None or await _membership(projects, room.project_id, user_id) is None:
        raise RoomNotFoundError(room_id)
    return room


async def box_of_member(
    boxes: AbstractBoxRepository,
    projects: AbstractProjectRepository,
    box_id: UUID,
    user_id: UUID,
) -> Box:
    box = await boxes.get(BoxId(box_id))
    if box is None or await _membership(projects, box.project_id, user_id) is None:
        raise BoxNotFoundError(box_id)
    return box


async def item_of_member(
    items: AbstractItemRepository,
    projects: AbstractProjectRepository,
    item_id: UUID,
    user_id: UUID,
) -> Item:
    item = await items.get(ItemId(item_id))
    if item is None or await _membership(projects, item.project_id, user_id) is None:
        raise ItemNotFoundError(item_id)
    return item


async def room_in_project(
    rooms: AbstractRoomRepository, room_id: UUID, project_id: ProjectId
) -> Room:
    """A room another object of `project_id` refers to: one of another project is
    reported as missing, like any object the caller cannot see."""
    room = await rooms.get(RoomId(room_id))
    if room is None or room.project_id != project_id:
        raise RoomNotFoundError(room_id)
    return room


async def box_in_project(boxes: AbstractBoxRepository, box_id: UUID, project_id: ProjectId) -> Box:
    box = await boxes.get(BoxId(box_id))
    if box is None or box.project_id != project_id:
        raise BoxNotFoundError(box_id)
    return box


async def box_view(
    read_model: AbstractInventoryReadModel, box_id: BoxId, viewer_id: UUID
) -> BoxDTO:
    """The view of a box the use case has just loaded or changed in this transaction."""
    view = await read_model.box(box_id, viewer_id=UserId(viewer_id))
    if view is None:
        raise BoxNotFoundError(box_id)
    return view


async def item_views(
    read_model: AbstractInventoryReadModel, item_ids: Sequence[ItemId]
) -> list[ItemDTO]:
    """Views of items the use case has just loaded or changed, in the given order."""
    views = await read_model.items_by_ids(item_ids)
    if len(views) != len(item_ids):
        missing = set(item_ids) - {view.id for view in views}
        raise ItemNotFoundError(next(iter(missing)))
    return views
