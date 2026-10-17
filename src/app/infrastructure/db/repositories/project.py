from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.exceptions import ProjectNotFoundError
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.domain.common.ids import ProjectId, UserId
from app.domain.projects.entity import Project
from app.infrastructure.db.mappers.project import ProjectMapper
from app.infrastructure.db.models.project import ProjectModel


class SqlAlchemyProjectRepository(AbstractProjectRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, project: Project) -> None:
        self._session.add(ProjectMapper.to_model(project))
        # Right away: rooms, notifications and journal entries refer to the project.
        await self._session.flush()

    async def get_by_id(self, project_id: ProjectId, *, for_update: bool = False) -> Project | None:
        # FOR UPDATE locks the projects row only; members and reminders are
        # loaded by separate SELECTs (selectin) and are protected by that lock,
        # since every change of the aggregate takes it first.
        model = await self._session.get(
            ProjectModel,
            project_id,
            with_for_update=for_update or None,
            populate_existing=for_update,
        )
        if model is None or not model.is_active:
            return None
        return ProjectMapper.to_domain(model)

    async def update(self, project: Project) -> None:
        # merge() alone would insert a missing project (see SqlAlchemyUserRepository).
        existing = await self._session.get(ProjectModel, project.id)
        if existing is None:
            raise ProjectNotFoundError(project.id)
        # Members and reminders follow the aggregate: delete-orphan removes the
        # rows that are gone, merge inserts the new ones.
        await self._session.merge(ProjectMapper.to_model(project))
        await self._session.flush()

    async def owns_active_projects(self, user_id: UserId) -> bool:
        owns = await self._session.scalar(
            select(exists().where(ProjectModel.owner_id == user_id, ProjectModel.is_active))
        )
        return bool(owns)

    async def lock_for_box_numbering(self, project_id: ProjectId) -> None:
        # FOR NO KEY UPDATE: conflicts with itself, so box creation in one project
        # queues up, but not with the FOR KEY SHARE that foreign key checks of other
        # inserts take (docs/db-design.md, section 6).
        await self._session.execute(
            select(ProjectModel.id)
            .where(ProjectModel.id == project_id)
            .with_for_update(key_share=True)
        )
