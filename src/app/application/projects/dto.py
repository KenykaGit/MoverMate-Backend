from dataclasses import dataclass
from datetime import date
from uuid import UUID

from app.application.common.patch import UNSET, Patch
from app.application.interfaces.project_reader import CollaboratorDTO
from app.domain.projects.value_objects import QrCodeStrategy


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateProjectDTO:
    owner_id: UUID
    name: str
    moving_date: date
    time_zone: str
    origin_address: str | None = None
    destination_address: str | None = None
    qr_code_strategy: QrCodeStrategy = QrCodeStrategy.PROTECTED
    reminder_offsets_days: tuple[int, ...] = ()
    source_rooms: tuple[str, ...] = ()
    destination_rooms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdateProjectDTO:
    """UNSET fields are left as they are; None clears an address."""

    project_id: UUID
    user_id: UUID
    name: Patch[str] = UNSET
    moving_date: Patch[date] = UNSET
    origin_address: Patch[str | None] = UNSET
    destination_address: Patch[str | None] = UNSET
    qr_code_strategy: Patch[QrCodeStrategy] = UNSET
    time_zone: Patch[str] = UNSET


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectActionDTO:
    """An action of `user_id` on a whole project."""

    project_id: UUID
    user_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class SetProjectRemindersDTO:
    project_id: UUID
    user_id: UUID
    offsets_days: tuple[int, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class AddCollaboratorDTO:
    project_id: UUID
    user_id: UUID
    """Who adds: any member may."""
    username: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RemoveCollaboratorDTO:
    project_id: UUID
    user_id: UUID
    """Who removes: any member may; removing oneself is leaving the project."""
    collaborator_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class AddedCollaboratorDTO:
    collaborator: CollaboratorDTO
    is_new: bool
    """False if the user already was a member: nothing changed."""
