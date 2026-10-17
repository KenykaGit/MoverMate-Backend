"""The read side of the overview screens: counters and checklists over a whole project.

Lists of boxes and items are paginated, so a client cannot count them itself
(frontend-backend-alignment.md, 2.4). Every method answers in one call: no N+1
requests per room or box. Like the inventory read model (ADR 0011), the port lives
in the feature because it returns the feature's DTOs.
"""

from abc import ABC, abstractmethod
from collections.abc import Collection

from app.application.summaries.dto import (
    BoxLabelDTO,
    LoadingChecklistRoomDTO,
    ProjectSummaryDTO,
    UnpackingProgressRoomDTO,
)
from app.domain.common.ids import BoxId, ProjectId


class AbstractSummaryReadModel(ABC):
    """Queries over one project; never change anything. Use cases check membership."""

    @abstractmethod
    async def project_summary(self, project_id: ProjectId) -> ProjectSummaryDTO:
        """Counters of boxes, items and tasks, taken from one snapshot."""

    @abstractmethod
    async def loading_checklist(self, project_id: ProjectId) -> list[LoadingChecklistRoomDTO]:
        """Every active source room, rooms without boxes too, by name (then id)."""

    @abstractmethod
    async def unpacking_progress(self, project_id: ProjectId) -> list[UnpackingProgressRoomDTO]:
        """Every active destination room, rooms without boxes too, by name (then id)."""

    @abstractmethod
    async def box_labels(
        self, project_id: ProjectId, box_ids: Collection[BoxId] | None = None
    ) -> list[BoxLabelDTO]:
        """Labels of the active boxes of the project, by number.

        With `box_ids`, only those of them; ids of other projects, deleted or unknown
        boxes are skipped.
        """
