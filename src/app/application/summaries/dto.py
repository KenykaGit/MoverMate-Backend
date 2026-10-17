from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from app.application.inventory.dto import BoxRefDTO, RoomRefDTO
from app.application.tasks.dto import TaskDTO
from app.domain.inventory.box import BoxStatus
from app.domain.inventory.item import ItemState
from app.domain.inventory.progress import is_source_room_loaded
from app.domain.tasks.value_objects import TaskPhase

# Requests


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectViewDTO:
    """`user_id` looks at a screen of the whole project."""

    user_id: UUID
    project_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class GetBoxLabelsDTO:
    user_id: UUID
    project_id: UUID
    box_ids: frozenset[UUID] | None = None
    """None: every active box of the project."""


# Views


@dataclass(frozen=True, slots=True, kw_only=True)
class DoneCountDTO:
    total: int
    done: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectSummaryDTO:
    """Counters over the active boxes, items and tasks of a project.

    Every mapping has a key for each value of its enum, zero included.
    """

    boxes_by_status: Mapping[BoxStatus, int]
    items_by_state: Mapping[ItemState, int]
    tasks_by_phase: Mapping[TaskPhase, DoneCountDTO]
    first_necessity: DoneCountDTO
    """Items of the first-day checklist; one is done when it is TAKEN_OUT."""
    next_task: TaskDTO | None
    """The first task not done yet: by phase, then due time (none last), then id."""

    @property
    def boxes_total(self) -> int:
        return sum(self.boxes_by_status.values())

    @property
    def items_total(self) -> int:
        return sum(self.items_by_state.values())

    @property
    def tasks(self) -> DoneCountDTO:
        counts = self.tasks_by_phase.values()
        return DoneCountDTO(total=sum(c.total for c in counts), done=sum(c.done for c in counts))


@dataclass(frozen=True, slots=True, kw_only=True)
class LoadingChecklistRoomDTO:
    """A room of the old home on the moving day."""

    room: RoomRefDTO
    to_pack_count: int
    """Items of the room still waiting to be packed."""
    boxes: tuple[BoxRefDTO, ...]
    """Boxes holding items of the room, by number."""

    @property
    def is_loaded(self) -> bool:
        return is_source_room_loaded(
            items_to_pack=self.to_pack_count, box_statuses=(box.status for box in self.boxes)
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class UnpackingProgressRoomDTO:
    """A room of the new home: how many of the boxes headed for it are unpacked."""

    room: RoomRefDTO
    total: int
    unpacked: int


@dataclass(frozen=True, slots=True, kw_only=True)
class BoxLabelDTO:
    """What a printed label of a box carries."""

    id: UUID
    number: int
    title: str | None
    is_fragile: bool
    destination_room: RoomRefDTO
    qr_token: str
    item_names: tuple[str, ...]
    """Active items in the box, in packing order: priority, then name."""
