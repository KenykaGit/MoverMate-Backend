"""Responses of the overview screens.

Counters per status are objects with a field for every value of the enum (zero
included), not free-form maps: a generated client gets exact types. A unit test
keeps the fields in line with the enums.
"""

from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field

from app.application.summaries.dto import (
    BoxLabelDTO,
    DoneCountDTO,
    LoadingChecklistRoomDTO,
    ProjectSummaryDTO,
    UnpackingProgressRoomDTO,
)
from app.presentation.http.schemas.inventory import BoxRef, RoomRef
from app.presentation.http.schemas.tasks import Task


class BoxStatusCounts(BaseModel):
    PACKING: int
    PACKED: int
    LOADED: int
    DELIVERED: int
    UNPACKED: int


class ItemStateCounts(BaseModel):
    TO_PACK: int
    IN_BOX: int
    TAKEN_OUT: int
    LOST: int


class DoneCount(BaseModel):
    total: int
    done: int

    @classmethod
    def from_dto(cls, count: DoneCountDTO) -> Self:
        return cls(total=count.total, done=count.done)


class TaskPhaseCounts(BaseModel):
    BEFORE: DoneCount
    DURING: DoneCount
    AFTER: DoneCount


class BoxesSummary(BaseModel):
    total: int
    by_status: BoxStatusCounts


class ItemsSummary(BaseModel):
    total: int
    by_state: ItemStateCounts


class TasksSummary(BaseModel):
    total: int
    done: int
    by_phase: TaskPhaseCounts


class FirstNecessitySummary(BaseModel):
    total: int = Field(description="Items with priority FIRST_NECESSITY")
    taken_out: int = Field(description="Of them, already TAKEN_OUT at the new home")


class ProjectSummary(BaseModel):
    boxes: BoxesSummary
    tasks: TasksSummary
    items: ItemsSummary
    first_necessity: FirstNecessitySummary
    next_task: Task | None = Field(
        description="The first task not done: by phase, then due_at (none last), then id"
    )

    @classmethod
    def from_dto(cls, summary: ProjectSummaryDTO) -> Self:
        tasks = summary.tasks
        return cls(
            boxes=BoxesSummary(
                total=summary.boxes_total,
                by_status=BoxStatusCounts.model_validate(
                    {status.value: n for status, n in summary.boxes_by_status.items()}
                ),
            ),
            tasks=TasksSummary(
                total=tasks.total,
                done=tasks.done,
                by_phase=TaskPhaseCounts.model_validate(
                    {
                        phase.value: DoneCount.from_dto(count)
                        for phase, count in summary.tasks_by_phase.items()
                    }
                ),
            ),
            items=ItemsSummary(
                total=summary.items_total,
                by_state=ItemStateCounts.model_validate(
                    {state.value: n for state, n in summary.items_by_state.items()}
                ),
            ),
            first_necessity=FirstNecessitySummary(
                total=summary.first_necessity.total, taken_out=summary.first_necessity.done
            ),
            next_task=None if summary.next_task is None else Task.from_dto(summary.next_task),
        )


class LoadingChecklistRoom(BaseModel):
    room: RoomRef = Field(description="A room of the old home (SOURCE)")
    to_pack_count: int = Field(description="Items of the room still waiting to be packed")
    boxes: list[BoxRef] = Field(description="Boxes holding items of the room, by number")
    is_loaded: bool = Field(
        description="Nothing left to pack and every such box is LOADED or further"
    )

    @classmethod
    def from_dto(cls, room: LoadingChecklistRoomDTO) -> Self:
        return cls(
            room=RoomRef.from_dto(room.room),
            to_pack_count=room.to_pack_count,
            boxes=[BoxRef.from_dto(box) for box in room.boxes],
            is_loaded=room.is_loaded,
        )


class UnpackingProgressRoom(BaseModel):
    room: RoomRef = Field(description="A room of the new home (DESTINATION)")
    total: int = Field(description="Boxes headed for the room")
    unpacked: int = Field(description="Of them, UNPACKED")

    @classmethod
    def from_dto(cls, room: UnpackingProgressRoomDTO) -> Self:
        return cls(room=RoomRef.from_dto(room.room), total=room.total, unpacked=room.unpacked)


class BoxLabel(BaseModel):
    id: UUID
    number: int
    title: str | None
    is_fragile: bool
    destination_room: RoomRef
    qr_token: str = Field(description="Encode `{frontend origin}/b/{qr_token}` in the QR code")
    item_names: list[str] = Field(description="In packing order: priority, then name")

    @classmethod
    def from_dto(cls, label: BoxLabelDTO) -> Self:
        return cls(
            id=label.id,
            number=label.number,
            title=label.title,
            is_fragile=label.is_fragile,
            destination_room=RoomRef.from_dto(label.destination_room),
            qr_token=label.qr_token,
            item_names=list(label.item_names),
        )
