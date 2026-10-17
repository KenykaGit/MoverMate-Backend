"""Loading the tasks the caller may touch. Helpers only read; the calling use case
owns the transaction."""

from collections.abc import Iterable
from uuid import UUID

from app.application.common.membership import member_project
from app.application.exceptions import AssigneeNotCollaboratorError, TaskNotFoundError
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.task_repository import AbstractTaskRepository
from app.application.tasks.dto import TaskDTO
from app.application.tasks.read_model import AbstractTaskReadModel
from app.domain.common.ids import TaskId, UserId
from app.domain.projects.entity import Project
from app.domain.tasks.entity import Task


async def task_of_member(
    tasks: AbstractTaskRepository,
    projects: AbstractProjectRepository,
    task_id: UUID,
    user_id: UUID,
    *,
    for_update: bool = False,
) -> tuple[Task, Project]:
    """The task and its project, if `user_id` is a member of that project.

    Raises:
        TaskNotFoundError: no such active task, or not a member (indistinguishable).
    """
    task = await tasks.get_by_id(TaskId(task_id), for_update=for_update)
    project = None if task is None else await member_project(projects, task.project_id, user_id)
    if task is None or project is None:
        raise TaskNotFoundError(task_id)
    return task, project


def assignees_in(project: Project, user_ids: Iterable[UUID]) -> set[UserId]:
    """`user_ids` as assignees of a task of `project`; duplicates collapse.

    Raises:
        AssigneeNotCollaboratorError: some of them are not members of the project.
    """
    assignees = {UserId(user_id) for user_id in user_ids}
    strangers = {user_id for user_id in assignees if not project.is_member(user_id)}
    if strangers:
        raise AssigneeNotCollaboratorError(strangers)
    return assignees


async def task_view(read_model: AbstractTaskReadModel, task_id: TaskId) -> TaskDTO:
    """The task as the API shows it; it was just loaded or written, so it exists."""
    [view] = await read_model.get_many([task_id])
    return view
