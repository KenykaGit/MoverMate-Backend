from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.exceptions import AssigneeNotCollaboratorError, TaskNotFoundError
from app.application.interfaces.task_repository import AbstractTaskRepository
from app.domain.common.ids import TaskId
from app.domain.tasks.entity import Task
from app.infrastructure.db.errors import violated_constraint
from app.infrastructure.db.mappers.task import TaskMapper
from app.infrastructure.db.models.task import ASSIGNEE_MEMBER_FK, TaskModel


class SqlAlchemyTaskRepository(AbstractTaskRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, task: Task) -> None:
        # Flushed right away: notifications and journal entries refer to the task,
        # and the read model shows it before the commit.
        async with self._flush_or_raise_not_member():
            self._session.add(TaskMapper.to_model(task))

    async def get_by_id(self, task_id: TaskId, *, for_update: bool = False) -> Task | None:
        # FOR UPDATE locks the tasks row only; assignees and reminders are loaded
        # by separate SELECTs (selectin) and are protected by that lock, since
        # every change of the aggregate takes it first.
        model = await self._session.get(
            TaskModel,
            task_id,
            with_for_update=for_update or None,
            populate_existing=for_update,
        )
        if model is None or not model.is_active:
            return None
        return TaskMapper.to_domain(model)

    async def update(self, task: Task) -> None:
        # merge() alone would insert a missing task (see SqlAlchemyUserRepository).
        if await self._session.get(TaskModel, task.id) is None:
            raise TaskNotFoundError(task.id)
        # Assignees and reminders follow the aggregate: delete-orphan removes the
        # rows that are gone, merge inserts the new ones.
        async with self._flush_or_raise_not_member():
            await self._session.merge(TaskMapper.to_model(task))

    @asynccontextmanager
    async def _flush_or_raise_not_member(self) -> AsyncIterator[None]:
        """Flushed inside a SAVEPOINT: an assignee who left the project meanwhile
        becomes an application error, and the rest of the transaction stays usable.

        The use case checks membership first; this catches the race with a member
        being removed concurrently, which the composite foreign key detects.
        """
        try:
            async with self._session.begin_nested():
                yield
        except IntegrityError as error:
            if violated_constraint(error) == ASSIGNEE_MEMBER_FK:
                raise AssigneeNotCollaboratorError from error
            raise
