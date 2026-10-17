"""Rooms of a project (api-endpoints.md, section 5)."""

from typing import Annotated
from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Query, status

from app.application.inventory.create_room import CreateRoom
from app.application.inventory.delete_room import DeleteRoom
from app.application.inventory.dto import DeleteRoomDTO, ListRoomsDTO
from app.application.inventory.list_rooms import ListRooms
from app.application.inventory.rename_room import RenameRoom
from app.domain.inventory.value_objects import RoomKind
from app.presentation.http.auth import CurrentUser
from app.presentation.http.errors import error_doc
from app.presentation.http.schemas.inventory import CreateRoomRequest, Room, UpdateRoomRequest

router = APIRouter(
    tags=["rooms"],
    route_class=DishkaRoute,
    responses=error_doc(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    | error_doc(status.HTTP_404_NOT_FOUND, "No such project or room, or not a member"),
)

_NAME_TAKEN = error_doc(status.HTTP_409_CONFLICT, "An active room of this kind has this name")


@router.get("/projects/{project_id}/rooms", operation_id="listRooms")
async def list_rooms(
    project_id: UUID,
    user: CurrentUser,
    list_rooms: FromDishka[ListRooms],
    kind: Annotated[RoomKind | None, Query(description="Both kinds when absent")] = None,
) -> list[Room]:
    """Active rooms of the project, by name."""
    rooms = await list_rooms(ListRoomsDTO(user_id=user.id, project_id=project_id, kind=kind))
    return [Room.from_dto(room) for room in rooms]


@router.post(
    "/projects/{project_id}/rooms",
    operation_id="createRoom",
    status_code=status.HTTP_201_CREATED,
    responses=_NAME_TAKEN,
)
async def create_room(
    project_id: UUID,
    body: CreateRoomRequest,
    user: CurrentUser,
    create_room: FromDishka[CreateRoom],
) -> Room:
    room = await create_room(body.to_dto(user_id=user.id, project_id=project_id))
    return Room.from_dto(room)


@router.patch("/rooms/{room_id}", operation_id="updateRoom", responses=_NAME_TAKEN)
async def update_room(
    room_id: UUID, body: UpdateRoomRequest, user: CurrentUser, rename_room: FromDishka[RenameRoom]
) -> Room:
    room = await rename_room(body.to_dto(user_id=user.id, room_id=room_id))
    return Room.from_dto(room)


@router.delete(
    "/rooms/{room_id}",
    operation_id="deleteRoom",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_doc(status.HTTP_409_CONFLICT, "Active boxes or items refer to the room"),
)
async def delete_room(
    room_id: UUID, user: CurrentUser, delete_room: FromDishka[DeleteRoom]
) -> None:
    await delete_room(DeleteRoomDTO(user_id=user.id, room_id=room_id))
