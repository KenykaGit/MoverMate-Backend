"""Read model of projects: what the API shows, including what the aggregate does
not hold (usernames, bookkeeping timestamps set by the database)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime

from app.domain.common.ids import ProjectId, UserId
from app.domain.projects.value_objects import QrCodeStrategy


@dataclass(frozen=True, slots=True, kw_only=True)
class UserRefDTO:
    id: UserId
    username: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectDTO:
    """A project as one of its members (the viewer) sees it."""

    id: ProjectId
    name: str
    moving_date: date
    origin_address: str | None
    destination_address: str | None
    qr_code_strategy: QrCodeStrategy
    time_zone: str
    reminder_offsets_days: tuple[int, ...]
    """Ascending."""
    owner: UserRefDTO
    is_owner: bool
    """The viewer owns the project: only they may delete it."""
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class CollaboratorDTO:
    user: UserRefDTO
    is_owner: bool
    created_at: datetime
    """When the user joined the project."""


class AbstractProjectReader(ABC):
    """Queries for responses. Reads what is committed or staged in the current
    transaction; never changes anything."""

    @abstractmethod
    async def get(self, project_id: ProjectId, *, viewer_id: UserId) -> ProjectDTO | None:
        """The active project if `viewer_id` is its member, else None."""

    @abstractmethod
    async def list_for_member(self, viewer_id: UserId) -> list[ProjectDTO]:
        """Active projects of the member, by moving date (then id: a stable order)."""

    @abstractmethod
    async def list_collaborators(self, project_id: ProjectId) -> list[CollaboratorDTO]:
        """Members with an active account: the owner first, then by joining time."""

    @abstractmethod
    async def get_collaborator(
        self, project_id: ProjectId, user_id: UserId
    ) -> CollaboratorDTO | None:
        """The member with an active account, or None."""
