from collections import defaultdict
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import ColumnElement, case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.project_reader import UserRefDTO
from app.application.tasks.dto import TaskDTO
from app.application.tasks.read_model import AbstractTaskReadModel
from app.domain.common.ids import ProjectId, TaskId, UserId
from app.domain.tasks.value_objects import TaskPhase
from app.infrastructure.db.models.task import TaskCollaboratorModel, TaskModel, TaskReminderModel
from app.infrastructure.db.models.user import UserModel

_T = TaskModel

# Phases in the order of the move, not alphabetical (AFTER < BEFORE < DURING).
_PHASE_ORDER = case({phase.value: n for n, phase in enumerate(TaskPhase)}, value=_T.phase)
# The plan screen: phase of the move, then due time (none last), then id.
_PLAN_ORDER = (_PHASE_ORDER, _T.due_at.asc().nulls_last(), _T.id)


class SqlAlchemyTaskReadModel(AbstractTaskReadModel):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def project_tasks(
        self, project_id: ProjectId, *, phase: TaskPhase | None, is_done: bool | None
    ) -> list[TaskDTO]:
        conditions = [_T.project_id == project_id]
        if phase is not None:
            conditions.append(_T.phase == phase.value)
        if is_done is not None:
            conditions.append(_T.is_done.is_(is_done))
        return await load_task_views(self._session, *conditions)

    async def get_many(self, task_ids: Sequence[TaskId]) -> list[TaskDTO]:
        found = {
            view.id: view for view in await load_task_views(self._session, _T.id.in_(task_ids))
        }
        return [found[task_id] for task_id in task_ids if task_id in found]


async def load_task_views(
    session: AsyncSession, *conditions: ColumnElement[bool], limit: int | None = None
) -> list[TaskDTO]:
    """Active tasks matching `conditions`, in plan order, as the API shows them.

    Three statements whatever the number of tasks: the rows, then the assignees
    and the reminders of all of them. Columns, not the ORM entity: no stale object
    of the identity map, and its selectin relationships are not loaded.
    """
    tasks = (
        await session.execute(
            select(
                _T.id,
                _T.title,
                _T.description,
                _T.phase,
                _T.due_at,
                _T.is_done,
                _T.done_at,
                _T.created_at,
                _T.updated_at,
            )
            .where(_T.is_active, *conditions)
            .order_by(*_PLAN_ORDER)
            .limit(limit)
        )
    ).all()
    if not tasks:
        return []
    ids = [task.id for task in tasks]

    # Like the list of collaborators, deleted accounts are hidden.
    assignees: defaultdict[UUID, list[UserRefDTO]] = defaultdict(list)
    for assignee in await session.execute(
        select(TaskCollaboratorModel.task_id, UserModel.id, UserModel.username)
        .join(UserModel, UserModel.id == TaskCollaboratorModel.user_id)
        .where(TaskCollaboratorModel.task_id.in_(ids), UserModel.is_active)
        .order_by(UserModel.username)
    ):
        assignees[assignee.task_id].append(
            UserRefDTO(id=UserId(assignee.id), username=assignee.username)
        )

    offsets: defaultdict[UUID, list[int]] = defaultdict(list)
    for reminder in await session.execute(
        select(TaskReminderModel.task_id, TaskReminderModel.offset_hours)
        .where(TaskReminderModel.task_id.in_(ids))
        .order_by(TaskReminderModel.offset_hours)
    ):
        offsets[reminder.task_id].append(reminder.offset_hours)

    return [
        TaskDTO(
            id=task.id,
            title=task.title,
            description=task.description,
            phase=TaskPhase(task.phase),
            due_at=task.due_at,
            is_done=task.is_done,
            done_at=task.done_at,
            assignees=tuple(assignees[task.id]),
            reminder_offsets_hours=tuple(offsets[task.id]),
            created_at=task.created_at,
            updated_at=task.updated_at,
        )
        for task in tasks
    ]
