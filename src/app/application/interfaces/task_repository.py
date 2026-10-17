from abc import ABC, abstractmethod

from app.domain.common.ids import TaskId
from app.domain.tasks.entity import Task


class AbstractTaskRepository(ABC):
    """Collection-like access to task aggregates (with assignees and reminders).

    Tasks are soft-deleted (Task.is_active is False) and then hidden from every
    lookup. Implementations must not commit: the unit of work owns the transaction.
    """

    @abstractmethod
    async def add(self, task: Task) -> None:
        """Stage a new task with its assignees and reminders.

        Raises:
            AssigneeNotCollaboratorError: an assignee is not a member of the project
                (the database checks it too, so a member leaving meanwhile is caught).
        """

    @abstractmethod
    async def get_by_id(self, task_id: TaskId, *, for_update: bool = False) -> Task | None:
        """The active task with this id, or None.

        `for_update` locks the task until the transaction ends: two concurrent
        changes of its assignees or reminders run one after the other.
        """

    @abstractmethod
    async def update(self, task: Task) -> None:
        """Stage changes of an existing task, including its assignees and reminders.

        Raises:
            TaskNotFoundError: there is no task with this id.
            AssigneeNotCollaboratorError: as in `add`.
        """
