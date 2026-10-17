"""Overview screens: only members read them; the rules on top of the read model."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from app.application.exceptions import ProjectNotFoundError
from app.application.inventory.dto import BoxRefDTO, RoomRefDTO
from app.application.summaries.dto import (
    DoneCountDTO,
    GetBoxLabelsDTO,
    LoadingChecklistRoomDTO,
    ProjectSummaryDTO,
    ProjectViewDTO,
)
from app.application.summaries.get_box_labels import GetBoxLabels
from app.application.summaries.get_loading_checklist import GetLoadingChecklist
from app.application.summaries.get_project_summary import GetProjectSummary
from app.application.summaries.get_unpacking_progress import GetUnpackingProgress
from app.domain.common.ids import UserId, new_id
from app.domain.inventory.box import BoxStatus
from app.domain.inventory.item import ItemState
from app.domain.projects.entity import Project
from app.domain.tasks.value_objects import TaskPhase
from tests.factories import make_project
from tests.fakes import FakeSummaryReadModel, InMemoryProjectRepository

OWNER = UserId(new_id())
MEMBER = UserId(new_id())
STRANGER = UserId(new_id())

type Call = Callable[[UserId], Awaitable[Any]]


@pytest.fixture
async def project(projects: InMemoryProjectRepository) -> Project:
    project = make_project(owner_id=OWNER)
    project.add_member(MEMBER)
    await projects.add(project)
    return project


@pytest.fixture(params=["summary", "loading_checklist", "unpacking_progress", "labels"])
def call(
    request: pytest.FixtureRequest,
    projects: InMemoryProjectRepository,
    summary_read_model: FakeSummaryReadModel,
    project: Project,
) -> Call:
    """Each use case of the feature, called by a given user on `project`."""

    def action(user_id: UserId) -> ProjectViewDTO:
        return ProjectViewDTO(user_id=user_id, project_id=project.id)

    match request.param:
        case "summary":
            return lambda user: GetProjectSummary(projects, summary_read_model)(action(user))
        case "loading_checklist":
            return lambda user: GetLoadingChecklist(projects, summary_read_model)(action(user))
        case "unpacking_progress":
            return lambda user: GetUnpackingProgress(projects, summary_read_model)(action(user))
        case _:
            return lambda user: GetBoxLabels(projects, summary_read_model)(
                GetBoxLabelsDTO(project_id=project.id, user_id=user)
            )


async def test_members_read(
    call: Call, summary_read_model: FakeSummaryReadModel, project: Project
) -> None:
    await call(OWNER)
    await call(MEMBER)

    assert [project_id for _, project_id in summary_read_model.queries] == [project.id, project.id]


async def test_strangers_get_the_404_of_a_missing_project(
    call: Call, summary_read_model: FakeSummaryReadModel
) -> None:
    with pytest.raises(ProjectNotFoundError):
        await call(STRANGER)

    assert summary_read_model.queries == []


async def test_deleted_project_is_not_found(
    call: Call,
    projects: InMemoryProjectRepository,
    summary_read_model: FakeSummaryReadModel,
    project: Project,
) -> None:
    project.soft_delete(by=OWNER)
    await projects.update(project)

    with pytest.raises(ProjectNotFoundError):
        await call(OWNER)

    assert summary_read_model.queries == []


@pytest.mark.parametrize("chosen", [None, frozenset(), frozenset({new_id(), new_id()})])
async def test_labels_pass_the_chosen_boxes_on(
    projects: InMemoryProjectRepository,
    summary_read_model: FakeSummaryReadModel,
    project: Project,
    chosen: frozenset[Any] | None,
) -> None:
    await GetBoxLabels(projects, summary_read_model)(
        GetBoxLabelsDTO(project_id=project.id, user_id=MEMBER, box_ids=chosen)
    )

    assert summary_read_model.label_box_ids == [chosen]


def box(status: BoxStatus) -> BoxRefDTO:
    return BoxRefDTO(
        id=new_id(),
        number=1,
        status=status,
        destination_room=RoomRefDTO(id=new_id(), name="Bedroom"),
    )


@pytest.mark.parametrize(
    ("to_pack", "statuses", "is_loaded"),
    [
        (0, [], True),
        (1, [], False),
        (0, [BoxStatus.LOADED, BoxStatus.UNPACKED], True),
        (0, [BoxStatus.LOADED, BoxStatus.PACKED], False),
        (2, [BoxStatus.DELIVERED], False),
    ],
)
def test_room_is_loaded_when_nothing_is_left_behind(
    to_pack: int, statuses: list[BoxStatus], is_loaded: bool
) -> None:
    room = LoadingChecklistRoomDTO(
        room=RoomRefDTO(id=new_id(), name="Kitchen"),
        to_pack_count=to_pack,
        boxes=tuple(box(status) for status in statuses),
    )

    assert room.is_loaded is is_loaded


def test_summary_totals_add_up_the_counters() -> None:
    summary = ProjectSummaryDTO(
        boxes_by_status=dict.fromkeys(BoxStatus, 2),
        items_by_state={state: n for n, state in enumerate(ItemState)},
        tasks_by_phase={
            TaskPhase.BEFORE: DoneCountDTO(total=3, done=1),
            TaskPhase.DURING: DoneCountDTO(total=0, done=0),
            TaskPhase.AFTER: DoneCountDTO(total=2, done=2),
        },
        first_necessity=DoneCountDTO(total=0, done=0),
        next_task=None,
    )

    assert summary.boxes_total == 2 * len(BoxStatus)
    assert summary.items_total == 0 + 1 + 2 + 3
    assert summary.tasks == DoneCountDTO(total=5, done=3)
