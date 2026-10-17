from collections import defaultdict
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Row, Select, case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.project_reader import (
    AbstractProjectReader,
    CollaboratorDTO,
    ProjectDTO,
    UserRefDTO,
)
from app.domain.common.ids import ProjectId, UserId
from app.domain.projects.value_objects import QrCodeStrategy
from app.infrastructure.db.models.project import (
    ProjectCollaboratorModel,
    ProjectModel,
    ProjectReminderModel,
)
from app.infrastructure.db.models.user import UserModel

type _Query = Select[*tuple[Any, ...]]
type _Row = Row[*tuple[Any, ...]]

_P = ProjectModel
_C = ProjectCollaboratorModel


class SqlAlchemyProjectReader(AbstractProjectReader):
    """Plain column queries, not ORM entities: the result is always what the
    database holds now, never a stale object of the identity map."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, project_id: ProjectId, *, viewer_id: UserId) -> ProjectDTO | None:
        rows = await self._projects(_visible_to(viewer_id).where(_P.id == project_id), viewer_id)
        return rows[0] if rows else None

    async def list_for_member(self, viewer_id: UserId) -> list[ProjectDTO]:
        return await self._projects(
            _visible_to(viewer_id).order_by(_P.moving_date, _P.id), viewer_id
        )

    async def list_collaborators(self, project_id: ProjectId) -> list[CollaboratorDTO]:
        rows = await self._session.execute(
            _collaborators(project_id).order_by(
                case((_C.user_id == _P.owner_id, 0), else_=1), _C.created_at, _C.user_id
            )
        )
        return [_collaborator(row) for row in rows]

    async def get_collaborator(
        self, project_id: ProjectId, user_id: UserId
    ) -> CollaboratorDTO | None:
        row = (
            await self._session.execute(_collaborators(project_id).where(_C.user_id == user_id))
        ).first()
        return None if row is None else _collaborator(row)

    async def _projects(self, query: _Query, viewer_id: UserId) -> list[ProjectDTO]:
        rows = (await self._session.execute(query)).all()
        offsets = await self._reminder_offsets([row.id for row in rows])
        return [_project(row, offsets.get(row.id, ()), viewer_id) for row in rows]

    async def _reminder_offsets(self, project_ids: Sequence[UUID]) -> dict[UUID, tuple[int, ...]]:
        if not project_ids:
            return {}
        rows = await self._session.execute(
            select(ProjectReminderModel.project_id, ProjectReminderModel.offset_days)
            .where(ProjectReminderModel.project_id.in_(project_ids))
            .order_by(ProjectReminderModel.project_id, ProjectReminderModel.offset_days)
        )
        offsets: defaultdict[UUID, list[int]] = defaultdict(list)
        for project_id, days in rows:
            offsets[project_id].append(days)
        return {project_id: tuple(days) for project_id, days in offsets.items()}


def _visible_to(viewer_id: UserId) -> _Query:
    """Active projects of which the viewer is a member, with their owner."""
    return (
        select(
            _P.id,
            _P.name,
            _P.moving_date,
            _P.origin_address,
            _P.destination_address,
            _P.qr_code_strategy,
            _P.time_zone,
            _P.owner_id,
            UserModel.username.label("owner_username"),
            _P.created_at,
            _P.updated_at,
        )
        .join(UserModel, UserModel.id == _P.owner_id)
        .join(_C, (_C.project_id == _P.id) & (_C.user_id == viewer_id))
        .where(_P.is_active)
    )


def _collaborators(project_id: ProjectId) -> _Query:
    # Members whose account is deleted are hidden, not removed: restoring the
    # account brings the membership back.
    return (
        select(_C.user_id, UserModel.username, _C.created_at, _P.owner_id)
        .join(UserModel, UserModel.id == _C.user_id)
        .join(_P, _P.id == _C.project_id)
        .where(_C.project_id == project_id, UserModel.is_active)
    )


def _project(row: _Row, offsets: tuple[int, ...], viewer_id: UserId) -> ProjectDTO:
    return ProjectDTO(
        id=ProjectId(row.id),
        name=row.name,
        moving_date=row.moving_date,
        origin_address=row.origin_address,
        destination_address=row.destination_address,
        qr_code_strategy=QrCodeStrategy(row.qr_code_strategy),
        time_zone=row.time_zone,
        reminder_offsets_days=offsets,
        owner=UserRefDTO(id=UserId(row.owner_id), username=row.owner_username),
        is_owner=row.owner_id == viewer_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _collaborator(row: _Row) -> CollaboratorDTO:
    return CollaboratorDTO(
        user=UserRefDTO(id=UserId(row.user_id), username=row.username),
        is_owner=row.user_id == row.owner_id,
        created_at=row.created_at,
    )
