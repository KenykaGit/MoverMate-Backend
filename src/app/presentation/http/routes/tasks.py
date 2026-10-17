"""Tasks of the plan and the templates to start them from (api-endpoints.md, section 8)."""

from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, status

from app.application.tasks.complete_task import CompleteTask
from app.application.tasks.create_task import CreateTask
from app.application.tasks.create_tasks_from_templates import CreateTasksFromTemplates
from app.application.tasks.delete_task import DeleteTask
from app.application.tasks.dto import ListTasksDTO, TaskActionDTO
from app.application.tasks.get_task import GetTask
from app.application.tasks.list_task_templates import ListTaskTemplates
from app.application.tasks.list_tasks import ListTasks
from app.application.tasks.reopen_task import ReopenTask
from app.application.tasks.set_task_assignees import SetTaskAssignees
from app.application.tasks.set_task_reminders import SetTaskReminders
from app.application.tasks.update_task import UpdateTask
from app.domain.tasks.value_objects import TaskPhase
from app.presentation.http.auth import CurrentUser
from app.presentation.http.errors import error_doc
from app.presentation.http.schemas.tasks import (
    CreateTaskRequest,
    CreateTasksFromTemplatesRequest,
    SetTaskAssigneesRequest,
    SetTaskRemindersRequest,
    Task,
    TaskTemplate,
    UpdateTaskRequest,
)

_UNAUTHORIZED = error_doc(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
_NO_DUE_TIME = error_doc(status.HTTP_409_CONFLICT, "Reminders need a due time")

templates_router = APIRouter(tags=["tasks"], route_class=DishkaRoute, responses=_UNAUTHORIZED)
router = APIRouter(
    tags=["tasks"],
    route_class=DishkaRoute,
    responses=_UNAUTHORIZED
    | error_doc(status.HTTP_404_NOT_FOUND, "No such project or task, or not a member"),
)


@templates_router.get("/task-templates", operation_id="listTaskTemplates")
async def list_task_templates(
    user: CurrentUser,  # noqa: ARG001  # signed-in users only
    list_task_templates: FromDishka[ListTaskTemplates],
) -> list[TaskTemplate]:
    """Ready-made tasks to add to a plan, by phase of the move."""
    templates = await list_task_templates()
    return [TaskTemplate.from_dto(template) for template in templates]


@router.post(
    "/projects/{project_id}/tasks/from-templates",
    operation_id="createTasksFromTemplates",
    status_code=status.HTTP_201_CREATED,
)
async def create_tasks_from_templates(
    project_id: UUID,
    body: CreateTasksFromTemplatesRequest,
    user: CurrentUser,
    create_tasks_from_templates: FromDishka[CreateTasksFromTemplates],
) -> list[Task]:
    """Copy the chosen templates into tasks, all or none (422 `unknown_task_template`).

    The copies keep no link to their templates: choosing one again adds another task.
    """
    tasks = await create_tasks_from_templates(body.to_dto(user_id=user.id, project_id=project_id))
    return [Task.from_dto(task) for task in tasks]


@router.get("/projects/{project_id}/tasks", operation_id="listTasks")
async def list_tasks(
    project_id: UUID,
    user: CurrentUser,
    list_tasks: FromDishka[ListTasks],
    phase: TaskPhase | None = None,
    is_done: bool | None = None,
) -> list[Task]:
    """Active tasks of the project by phase of the move, due time (none last) and id."""
    tasks = await list_tasks(
        ListTasksDTO(user_id=user.id, project_id=project_id, phase=phase, is_done=is_done)
    )
    return [Task.from_dto(task) for task in tasks]


@router.post(
    "/projects/{project_id}/tasks",
    operation_id="createTask",
    status_code=status.HTTP_201_CREATED,
    responses=_NO_DUE_TIME,
)
async def create_task(
    project_id: UUID,
    body: CreateTaskRequest,
    user: CurrentUser,
    create_task: FromDishka[CreateTask],
) -> Task:
    """A new task; assignees other than the creator get TASK_ASSIGNED.

    Assignees must be members of the project (422 `assignee_not_collaborator`).
    """
    task = await create_task(body.to_dto(user_id=user.id, project_id=project_id))
    return Task.from_dto(task)


@router.get("/tasks/{task_id}", operation_id="getTask")
async def get_task(task_id: UUID, user: CurrentUser, get_task: FromDishka[GetTask]) -> Task:
    task = await get_task(TaskActionDTO(user_id=user.id, task_id=task_id))
    return Task.from_dto(task)


@router.patch("/tasks/{task_id}", operation_id="updateTask")
async def update_task(
    task_id: UUID, body: UpdateTaskRequest, user: CurrentUser, update_task: FromDishka[UpdateTask]
) -> Task:
    """A new `due_at` moves the pending reminders; `due_at: null` removes them."""
    task = await update_task(body.to_dto(user_id=user.id, task_id=task_id))
    return Task.from_dto(task)


@router.post("/tasks/{task_id}/complete", operation_id="completeTask")
async def complete_task(
    task_id: UUID, user: CurrentUser, complete_task: FromDishka[CompleteTask]
) -> Task:
    """Idempotent: a task already done keeps its first `done_at`."""
    task = await complete_task(TaskActionDTO(user_id=user.id, task_id=task_id))
    return Task.from_dto(task)


@router.post("/tasks/{task_id}/reopen", operation_id="reopenTask")
async def reopen_task(
    task_id: UUID, user: CurrentUser, reopen_task: FromDishka[ReopenTask]
) -> Task:
    """Idempotent: a task that is not done stays as it is."""
    task = await reopen_task(TaskActionDTO(user_id=user.id, task_id=task_id))
    return Task.from_dto(task)


@router.put("/tasks/{task_id}/assignees", operation_id="setTaskAssignees")
async def set_task_assignees(
    task_id: UUID,
    body: SetTaskAssigneesRequest,
    user: CurrentUser,
    set_task_assignees: FromDishka[SetTaskAssignees],
) -> Task:
    """Replace the assignees; new ones other than the caller get TASK_ASSIGNED.

    Assignees must be members of the project (422 `assignee_not_collaborator`).
    """
    task = await set_task_assignees(body.to_dto(user_id=user.id, task_id=task_id))
    return Task.from_dto(task)


@router.put("/tasks/{task_id}/reminders", operation_id="setTaskReminders", responses=_NO_DUE_TIME)
async def set_task_reminders(
    task_id: UUID,
    body: SetTaskRemindersRequest,
    user: CurrentUser,
    set_task_reminders: FromDishka[SetTaskReminders],
) -> Task:
    """Replace the reminders (hours before `due_at`); an empty list removes them all."""
    task = await set_task_reminders(body.to_dto(user_id=user.id, task_id=task_id))
    return Task.from_dto(task)


@router.delete(
    "/tasks/{task_id}", operation_id="deleteTask", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_task(
    task_id: UUID, user: CurrentUser, delete_task: FromDishka[DeleteTask]
) -> None:
    """Pending notifications about the task are cancelled."""
    await delete_task(TaskActionDTO(user_id=user.id, task_id=task_id))
