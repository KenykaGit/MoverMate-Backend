from abc import ABC, abstractmethod

from app.domain.common.ids import ProjectId, UserId
from app.domain.projects.entity import Project


class AbstractProjectRepository(ABC):
    """Collection-like access to project aggregates (with members and reminders).

    Projects are soft-deleted (Project.is_active is False) and then hidden from
    every lookup. Implementations must not commit: the unit of work owns the
    transaction.
    """

    @abstractmethod
    async def add(self, project: Project) -> None:
        """Stage a new project with its members and reminders."""

    @abstractmethod
    async def get_by_id(self, project_id: ProjectId, *, for_update: bool = False) -> Project | None:
        """The active project with this id, or None.

        `for_update` locks the project until the transaction ends: concurrent
        changes of the same aggregate (two members added at once) run one after
        the other instead of overwriting each other.
        """

    @abstractmethod
    async def update(self, project: Project) -> None:
        """Stage changes of an existing project, including its members and reminders.

        Raises:
            ProjectNotFoundError: there is no project with this id.
        """

    @abstractmethod
    async def owns_active_projects(self, user_id: UserId) -> bool:
        """Whether the user owns at least one active project."""

    @abstractmethod
    async def lock_for_box_numbering(self, project_id: ProjectId) -> None:
        """Serialize box creation in the project until the transaction ends.

        Two concurrent transactions would otherwise both see the same lowest free
        number. Unlike `get_by_id(for_update=True)`, this lock does not block the
        foreign key checks of other inserts into the project, so items and rooms
        are still created meanwhile (docs/db-design.md, section 6).
        """
