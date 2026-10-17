"""Boxes and their QR codes (api-endpoints.md, sections 6 and 9)."""

from typing import Annotated
from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Query, status

from app.application.common.pagination import MAX_PAGE_SIZE, CursorPageRequest
from app.application.inventory.cancel_contents_reminder import CancelContentsReminder
from app.application.inventory.change_box_status import ChangeBoxStatus
from app.application.inventory.create_box import CreateBox
from app.application.inventory.delete_box import DeleteBox
from app.application.inventory.dto import (
    CancelContentsReminderDTO,
    DeleteBoxDTO,
    GetBoxByQrDTO,
    GetBoxDTO,
    ListBoxesDTO,
)
from app.application.inventory.get_box import GetBox
from app.application.inventory.get_box_by_qr import GetBoxByQr
from app.application.inventory.list_boxes import ListBoxes
from app.application.inventory.schedule_contents_reminder import ScheduleContentsReminder
from app.application.inventory.update_box import UpdateBox
from app.domain.inventory.box import BoxStatus
from app.presentation.http.auth import CurrentUser, OptionalUser
from app.presentation.http.errors import error_doc
from app.presentation.http.schemas.inventory import (
    Box,
    BoxByQr,
    BoxPage,
    CreateBoxRequest,
    SetBoxStatusRequest,
    SetContentsReminderRequest,
    UpdateBoxRequest,
)

PAGE_LIMIT_DEFAULT = 50

_UNAUTHORIZED = error_doc(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
_NOT_CREATOR = error_doc(status.HTTP_403_FORBIDDEN, "Only the creator of the box")

router = APIRouter(
    tags=["boxes"],
    route_class=DishkaRoute,
    responses=_UNAUTHORIZED
    | error_doc(status.HTTP_404_NOT_FOUND, "No such project, box or room, or not a member"),
)


@router.get("/projects/{project_id}/boxes", operation_id="listBoxes")
async def list_boxes(
    project_id: UUID,
    user: CurrentUser,
    list_boxes: FromDishka[ListBoxes],
    statuses: Annotated[
        list[BoxStatus] | None, Query(alias="status", description="Repeat to match several")
    ] = None,
    destination_room_id: UUID | None = None,
    number: Annotated[
        int | None, Query(description="Exact number, UNPACKED boxes included")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = PAGE_LIMIT_DEFAULT,
    after: Annotated[str | None, Query(description="next_cursor of the previous page")] = None,
) -> BoxPage:
    """Active boxes of the project, by number."""
    page = await list_boxes(
        ListBoxesDTO(
            user_id=user.id,
            project_id=project_id,
            statuses=tuple(statuses or ()),
            destination_room_id=destination_room_id,
            number=number,
            page=CursorPageRequest(limit=limit, after=after),
        )
    )
    return BoxPage.from_dto(page)


@router.post(
    "/projects/{project_id}/boxes",
    operation_id="createBox",
    status_code=status.HTTP_201_CREATED,
)
async def create_box(
    project_id: UUID, body: CreateBoxRequest, user: CurrentUser, create_box: FromDishka[CreateBox]
) -> Box:
    """A new box in PACKING with the lowest free number; `items` are packed into it
    in the same transaction."""
    box = await create_box(body.to_dto(user_id=user.id, project_id=project_id))
    return Box.from_dto(box)


@router.get("/boxes/by-qr/{qr_token}", operation_id="getBoxByQr")
async def get_box_by_qr(
    qr_token: str, user: OptionalUser, get_box_by_qr: FromDishka[GetBoxByQr]
) -> BoxByQr:
    """Open a box card by the token of its QR label.

    PROTECTED project: members get WRITE, anonymous callers 401, other users 404.
    OPEN project: members get WRITE, everyone else READ. A deleted box is 404.
    """
    found = await get_box_by_qr(
        GetBoxByQrDTO(user_id=None if user is None else user.id, qr_token=qr_token)
    )
    return BoxByQr.from_dto(found)


@router.get("/boxes/{box_id}", operation_id="getBox")
async def get_box(box_id: UUID, user: CurrentUser, get_box: FromDishka[GetBox]) -> Box:
    box = await get_box(GetBoxDTO(user_id=user.id, box_id=box_id))
    return Box.from_dto(box)


@router.patch("/boxes/{box_id}", operation_id="updateBox")
async def update_box(
    box_id: UUID, body: UpdateBoxRequest, user: CurrentUser, update_box: FromDishka[UpdateBox]
) -> Box:
    box = await update_box(body.to_dto(user_id=user.id, box_id=box_id))
    return Box.from_dto(box)


@router.put(
    "/boxes/{box_id}/status",
    operation_id="setBoxStatus",
    responses=error_doc(status.HTTP_409_CONFLICT, "Not a single step, or changed concurrently"),
)
async def set_box_status(
    box_id: UUID,
    body: SetBoxStatusRequest,
    user: CurrentUser,
    change_box_status: FromDishka[ChangeBoxStatus],
) -> Box:
    """One step forward or back. The current status again is 200 without changes."""
    box = await change_box_status(body.to_dto(user_id=user.id, box_id=box_id))
    return Box.from_dto(box)


@router.delete("/boxes/{box_id}", operation_id="deleteBox", status_code=status.HTTP_204_NO_CONTENT)
async def delete_box(box_id: UUID, user: CurrentUser, delete_box: FromDishka[DeleteBox]) -> None:
    """Items in the box are deleted with it; its number becomes free."""
    await delete_box(DeleteBoxDTO(user_id=user.id, box_id=box_id))


@router.put(
    "/boxes/{box_id}/contents-reminder",
    operation_id="setBoxContentsReminder",
    responses=_NOT_CREATOR | error_doc(status.HTTP_409_CONFLICT, "The box is not PACKING"),
)
async def set_box_contents_reminder(
    box_id: UUID,
    body: SetContentsReminderRequest,
    user: CurrentUser,
    schedule_contents_reminder: FromDishka[ScheduleContentsReminder],
) -> Box:
    """Remind the creator of the box to fill in its contents at `remind_at`."""
    box = await schedule_contents_reminder(body.to_dto(user_id=user.id, box_id=box_id))
    return Box.from_dto(box)


@router.delete(
    "/boxes/{box_id}/contents-reminder",
    operation_id="cancelBoxContentsReminder",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_NOT_CREATOR,
)
async def cancel_box_contents_reminder(
    box_id: UUID, user: CurrentUser, cancel_contents_reminder: FromDishka[CancelContentsReminder]
) -> None:
    await cancel_contents_reminder(CancelContentsReminderDTO(user_id=user.id, box_id=box_id))
