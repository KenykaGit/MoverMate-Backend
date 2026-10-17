from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Response, status

from app.application.common.idempotency import IdempotentExecutor
from app.application.interfaces.idempotency import JsonValue
from app.application.projects.add_collaborator import AddCollaborator
from app.application.projects.create_project import CreateProject
from app.application.projects.delete_project import DeleteProject
from app.application.projects.dto import (
    AddCollaboratorDTO,
    ProjectActionDTO,
    RemoveCollaboratorDTO,
    SetProjectRemindersDTO,
)
from app.application.projects.get_project import GetProject
from app.application.projects.list_collaborators import ListCollaborators
from app.application.projects.list_projects import ListProjects
from app.application.projects.remove_collaborator import RemoveCollaborator
from app.application.projects.set_project_reminders import SetProjectReminders
from app.application.projects.update_project import UpdateProject
from app.presentation.http.auth import CurrentUser
from app.presentation.http.errors import OpenApiResponses, error_doc
from app.presentation.http.idempotency import IdempotencyKey, run_idempotent
from app.presentation.http.schemas.projects import (
    AddCollaboratorRequest,
    CollaboratorResponse,
    CreateProjectRequest,
    ProjectResponse,
    SetProjectRemindersRequest,
    UpdateProjectRequest,
)

router = APIRouter(
    prefix="/projects",
    tags=["projects"],
    route_class=DishkaRoute,
    responses=error_doc(status.HTTP_401_UNAUTHORIZED, "Not authenticated"),
)

# Strangers get the same 404 as for a missing project: they must not learn it exists.
_NOT_FOUND = error_doc(status.HTTP_404_NOT_FOUND, "No such project, or not a member")
_ALREADY_MEMBER: OpenApiResponses = {
    status.HTTP_200_OK: {"model": CollaboratorResponse, "description": "Already a member"}
}


@router.get("", operation_id="listProjects")
async def list_projects(
    user: CurrentUser, list_projects: FromDishka[ListProjects]
) -> list[ProjectResponse]:
    """Active projects of the current user, by moving date."""
    return [ProjectResponse.from_dto(project) for project in await list_projects(user.id)]


@router.post(
    "",
    operation_id="createProject",
    status_code=status.HTTP_201_CREATED,
    responses=error_doc(status.HTTP_409_CONFLICT, "Two rooms of one kind share a name"),
)
async def create_project(
    body: CreateProjectRequest,
    user: CurrentUser,
    response: Response,
    create_project: FromDishka[CreateProject],
    executor: FromDishka[IdempotentExecutor],
    idempotency_key: IdempotencyKey = None,
) -> ProjectResponse:
    """The current user becomes the owner and first member. Rooms are created in
    the same transaction; task templates are not copied."""

    async def action() -> JsonValue:
        project = await create_project(body.to_dto(owner_id=user.id))
        created: JsonValue = ProjectResponse.from_dto(project).model_dump(mode="json")
        return created

    value = await run_idempotent(
        executor,
        scope=f"create_project:{user.id}",
        key=idempotency_key,
        payload=body.model_dump(mode="json"),
        response=response,
        action=action,
    )
    return ProjectResponse.model_validate(value)


@router.get("/{project_id}", operation_id="getProject", responses=_NOT_FOUND)
async def get_project(
    project_id: UUID, user: CurrentUser, get_project: FromDishka[GetProject]
) -> ProjectResponse:
    project = await get_project(ProjectActionDTO(project_id=project_id, user_id=user.id))
    return ProjectResponse.from_dto(project)


@router.patch("/{project_id}", operation_id="updateProject", responses=_NOT_FOUND)
async def update_project(
    project_id: UUID,
    body: UpdateProjectRequest,
    user: CurrentUser,
    update_project: FromDishka[UpdateProject],
) -> ProjectResponse:
    """Any member may. A new moving date or time zone moves pending reminders."""
    project = await update_project(body.to_dto(project_id=project_id, user_id=user.id))
    return ProjectResponse.from_dto(project)


@router.delete(
    "/{project_id}",
    operation_id="deleteProject",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_NOT_FOUND | error_doc(status.HTTP_403_FORBIDDEN, "Only the owner may delete"),
)
async def delete_project(
    project_id: UUID, user: CurrentUser, delete_project: FromDishka[DeleteProject]
) -> None:
    """Soft-delete with everything inside; final for users. Owner only."""
    await delete_project(ProjectActionDTO(project_id=project_id, user_id=user.id))


@router.put("/{project_id}/reminders", operation_id="setProjectReminders", responses=_NOT_FOUND)
async def set_project_reminders(
    project_id: UUID,
    body: SetProjectRemindersRequest,
    user: CurrentUser,
    set_project_reminders: FromDishka[SetProjectReminders],
) -> ProjectResponse:
    """Replace the moving date reminders; the same set again changes nothing."""
    project = await set_project_reminders(
        SetProjectRemindersDTO(
            project_id=project_id, user_id=user.id, offsets_days=tuple(body.offsets_days)
        )
    )
    return ProjectResponse.from_dto(project)


@router.get("/{project_id}/collaborators", operation_id="listCollaborators", responses=_NOT_FOUND)
async def list_collaborators(
    project_id: UUID, user: CurrentUser, list_collaborators: FromDishka[ListCollaborators]
) -> list[CollaboratorResponse]:
    """Members of the project, the owner first."""
    collaborators = await list_collaborators(
        ProjectActionDTO(project_id=project_id, user_id=user.id)
    )
    return [CollaboratorResponse.from_dto(collaborator) for collaborator in collaborators]


@router.post(
    "/{project_id}/collaborators",
    operation_id="addCollaborator",
    status_code=status.HTTP_201_CREATED,
    responses=_ALREADY_MEMBER | error_doc(status.HTTP_404_NOT_FOUND, "No such project or user"),
)
async def add_collaborator(
    project_id: UUID,
    body: AddCollaboratorRequest,
    user: CurrentUser,
    response: Response,
    add_collaborator: FromDishka[AddCollaborator],
) -> CollaboratorResponse:
    """Add a user by username; any member may. Adding a member again is a no-op (200)."""
    added = await add_collaborator(
        AddCollaboratorDTO(project_id=project_id, user_id=user.id, username=body.username)
    )
    if not added.is_new:
        response.status_code = status.HTTP_200_OK
    return CollaboratorResponse.from_dto(added.collaborator)


@router.delete(
    "/{project_id}/collaborators/{user_id}",
    operation_id="removeCollaborator",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_NOT_FOUND | error_doc(status.HTTP_409_CONFLICT, "The owner cannot be removed"),
)
async def remove_collaborator(
    project_id: UUID,
    user_id: UUID,
    user: CurrentUser,
    remove_collaborator: FromDishka[RemoveCollaborator],
) -> None:
    """Any member may remove any other member except the owner; one's own id means
    leaving the project. Removing a non-member is a no-op."""
    await remove_collaborator(
        RemoveCollaboratorDTO(project_id=project_id, user_id=user.id, collaborator_id=user_id)
    )
