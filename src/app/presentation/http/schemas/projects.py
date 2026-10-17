from datetime import date, datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from pydantic.json_schema import SkipJsonSchema

from app.application.common.patch import UNSET
from app.application.interfaces.project_reader import CollaboratorDTO, ProjectDTO, UserRefDTO
from app.application.projects.dto import CreateProjectDTO, UpdateProjectDTO
from app.domain.projects.value_objects import QrCodeStrategy
from app.presentation.http.schemas.common import RequestSchema


class UserRefResponse(BaseModel):
    id: UUID
    username: str

    @classmethod
    def from_dto(cls, user: UserRefDTO) -> Self:
        return cls(id=user.id, username=user.username)


class ProjectResponse(BaseModel):
    id: UUID
    name: str
    moving_date: date
    origin_address: str | None
    destination_address: str | None
    qr_code_strategy: QrCodeStrategy
    time_zone: str = Field(examples=["Europe/Moscow"], description="IANA time zone name")
    reminder_offsets_days: list[int] = Field(
        description="Days before the move when every member is reminded (09:00 local time)"
    )
    owner: UserRefResponse
    is_owner: bool = Field(description="The current user owns the project and may delete it")
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_dto(cls, project: ProjectDTO) -> Self:
        return cls(
            id=project.id,
            name=project.name,
            moving_date=project.moving_date,
            origin_address=project.origin_address,
            destination_address=project.destination_address,
            qr_code_strategy=project.qr_code_strategy,
            time_zone=project.time_zone,
            reminder_offsets_days=list(project.reminder_offsets_days),
            owner=UserRefResponse.from_dto(project.owner),
            is_owner=project.is_owner,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )


class CollaboratorResponse(BaseModel):
    user: UserRefResponse
    is_owner: bool
    created_at: datetime = Field(description="When the user joined the project")

    @classmethod
    def from_dto(cls, collaborator: CollaboratorDTO) -> Self:
        return cls(
            user=UserRefResponse.from_dto(collaborator.user),
            is_owner=collaborator.is_owner,
            created_at=collaborator.created_at,
        )


class ProjectRoomsRequest(RequestSchema):
    source: list[str] = Field(default_factory=list, description="Rooms of the old home")
    destination: list[str] = Field(default_factory=list, description="Rooms of the new home")


class CreateProjectRequest(RequestSchema):
    name: str
    moving_date: date
    time_zone: str = Field(examples=["Europe/Moscow"], description="IANA time zone name")
    origin_address: str | None = None
    destination_address: str | None = None
    qr_code_strategy: QrCodeStrategy = QrCodeStrategy.PROTECTED
    reminder_offsets_days: list[int] = Field(default_factory=list)
    rooms: ProjectRoomsRequest = Field(default_factory=ProjectRoomsRequest)

    def to_dto(self, *, owner_id: UUID) -> CreateProjectDTO:
        return CreateProjectDTO(
            owner_id=owner_id,
            name=self.name,
            moving_date=self.moving_date,
            time_zone=self.time_zone,
            origin_address=self.origin_address,
            destination_address=self.destination_address,
            qr_code_strategy=self.qr_code_strategy,
            reminder_offsets_days=tuple(self.reminder_offsets_days),
            source_rooms=tuple(self.rooms.source),
            destination_rooms=tuple(self.rooms.destination),
        )


# Fields that may be omitted but not cleared: None is only their "not sent" default,
# hidden from the schema; an explicit null is rejected below.
type _NotNull[T] = T | SkipJsonSchema[None]


class UpdateProjectRequest(RequestSchema):
    """Partial update: omitted fields stay as they are, null clears an address."""

    name: _NotNull[str] = None
    moving_date: _NotNull[date] = None
    time_zone: _NotNull[str] = Field(default=None, examples=["Europe/Moscow"])
    qr_code_strategy: _NotNull[QrCodeStrategy] = None
    origin_address: str | None = None
    destination_address: str | None = None

    @field_validator("name", "moving_date", "time_zone", "qr_code_strategy")
    @classmethod
    def _not_null(cls, value: object) -> object:
        # Defaults are not validated, so this runs for an explicit value only.
        if value is None:
            raise ValueError("may be omitted, but not null")
        return value

    def to_dto(self, *, project_id: UUID, user_id: UUID) -> UpdateProjectDTO:
        sent = self.model_fields_set
        return UpdateProjectDTO(
            project_id=project_id,
            user_id=user_id,
            name=self.name if "name" in sent and self.name is not None else UNSET,
            moving_date=(
                self.moving_date
                if "moving_date" in sent and self.moving_date is not None
                else UNSET
            ),
            time_zone=self.time_zone
            if "time_zone" in sent and self.time_zone is not None
            else UNSET,
            qr_code_strategy=(
                self.qr_code_strategy
                if "qr_code_strategy" in sent and self.qr_code_strategy is not None
                else UNSET
            ),
            origin_address=self.origin_address if "origin_address" in sent else UNSET,
            destination_address=self.destination_address
            if "destination_address" in sent
            else UNSET,
        )


class SetProjectRemindersRequest(RequestSchema):
    offsets_days: list[int] = Field(
        description="Days before the move, 0 (the moving day) to 365; replaces the current set"
    )


class AddCollaboratorRequest(RequestSchema):
    username: str
