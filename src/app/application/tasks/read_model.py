"""The read side of tasks: what the plan screen shows.

Like the inventory read model (ADR 0011), the port lives in the feature because it
returns the feature's DTOs. Use cases check membership before asking it.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from app.application.tasks.dto import TaskDTO
from app.domain.common.ids import ProjectId, TaskId
from app.domain.tasks.value_objects import TaskPhase


class AbstractTaskReadModel(ABC):
    @abstractmethod
    async def project_tasks(
        self, project_id: ProjectId, *, phase: TaskPhase | None, is_done: bool | None
    ) -> list[TaskDTO]:
        """Active tasks of the project in plan order: phase of the move (BEFORE,
        DURING, AFTER), then due time (none last), then id. A short list: no pages."""

    @abstractmethod
    async def get_many(self, task_ids: Sequence[TaskId]) -> list[TaskDTO]:
        """The active tasks among `task_ids`, in the given order."""
