"""Read-only views over a whole project for the overview, progress and labels screens."""

from typing import Annotated
from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Query, status

from app.application.summaries.dto import GetBoxLabelsDTO, ProjectViewDTO
from app.application.summaries.get_box_labels import GetBoxLabels
from app.application.summaries.get_loading_checklist import GetLoadingChecklist
from app.application.summaries.get_project_summary import GetProjectSummary
from app.application.summaries.get_unpacking_progress import GetUnpackingProgress
from app.presentation.http.auth import CurrentUser
from app.presentation.http.errors import error_doc
from app.presentation.http.schemas.summaries import (
    BoxLabel,
    LoadingChecklistRoom,
    ProjectSummary,
    UnpackingProgressRoom,
)

# Box ids travel in the query string: 100 of them still fit nginx's 8 KiB line.
MAX_LABELS_PER_REQUEST = 100

router = APIRouter(
    prefix="/projects",
    tags=["summaries"],
    route_class=DishkaRoute,
    responses=error_doc(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    | error_doc(status.HTTP_404_NOT_FOUND, "No such project, or not a member"),
)


@router.get("/{project_id}/summary", operation_id="getProjectSummary")
async def get_project_summary(
    project_id: UUID, user: CurrentUser, get_project_summary: FromDishka[GetProjectSummary]
) -> ProjectSummary:
    """Counters of active boxes, items and tasks, and the next task to do."""
    summary = await get_project_summary(ProjectViewDTO(user_id=user.id, project_id=project_id))
    return ProjectSummary.from_dto(summary)


@router.get("/{project_id}/loading-checklist", operation_id="getLoadingChecklist")
async def get_loading_checklist(
    project_id: UUID, user: CurrentUser, get_loading_checklist: FromDishka[GetLoadingChecklist]
) -> list[LoadingChecklistRoom]:
    """Every room of the old home, by name: what is left to pack, which boxes hold
    its items, and whether it is loaded. A room without items counts as loaded."""
    rooms = await get_loading_checklist(ProjectViewDTO(user_id=user.id, project_id=project_id))
    return [LoadingChecklistRoom.from_dto(room) for room in rooms]


@router.get("/{project_id}/unpacking-progress", operation_id="getUnpackingProgress")
async def get_unpacking_progress(
    project_id: UUID,
    user: CurrentUser,
    get_unpacking_progress: FromDishka[GetUnpackingProgress],
) -> list[UnpackingProgressRoom]:
    """Every room of the new home, by name: unpacked boxes out of those headed for it."""
    rooms = await get_unpacking_progress(ProjectViewDTO(user_id=user.id, project_id=project_id))
    return [UnpackingProgressRoom.from_dto(room) for room in rooms]


@router.get("/{project_id}/labels", operation_id="getBoxLabels")
async def get_box_labels(
    project_id: UUID,
    user: CurrentUser,
    get_box_labels: FromDishka[GetBoxLabels],
    box_ids: Annotated[
        list[UUID] | None,
        Query(
            max_length=MAX_LABELS_PER_REQUEST,
            description="Repeat to print chosen boxes (`?box_ids=…&box_ids=…`); "
            "omit for every box. Ids that are not active boxes of the project are skipped.",
        ),
    ] = None,
) -> list[BoxLabel]:
    """Labels to print, by box number, in one request."""
    labels = await get_box_labels(
        GetBoxLabelsDTO(
            project_id=project_id,
            user_id=user.id,
            box_ids=None if box_ids is None else frozenset(box_ids),
        )
    )
    return [BoxLabel.from_dto(label) for label in labels]
