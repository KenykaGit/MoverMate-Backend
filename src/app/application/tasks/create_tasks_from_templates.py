import logging

from app.application.common.clock import utc_now
from app.application.common.membership import project_of_member
from app.application.common.uow import AbstractUnitOfWork
from app.application.exceptions import UnknownTaskTemplateError
from app.application.interfaces.audit_log_repository import AbstractAuditLogRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.task_repository import AbstractTaskRepository
from app.application.interfaces.task_templates import AbstractTaskTemplateCatalog
from app.application.tasks.dto import CreateTasksFromTemplatesDTO, TaskDTO
from app.application.tasks.read_model import AbstractTaskReadModel
from app.domain.audit.entity import AuditAction, AuditEntry
from app.domain.common.ids import UserId
from app.domain.tasks.entity import Task
from app.domain.tasks.value_objects import TaskTemplateId

logger = logging.getLogger(__name__)


class CreateTasksFromTemplates:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        tasks: AbstractTaskRepository,
        templates: AbstractTaskTemplateCatalog,
        audit_log: AbstractAuditLogRepository,
        read_model: AbstractTaskReadModel,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._tasks = tasks
        self._templates = templates
        self._audit_log = audit_log
        self._read_model = read_model
        self._uow = uow

    async def __call__(self, data: CreateTasksFromTemplatesDTO) -> list[TaskDTO]:
        """Copy the chosen templates into tasks of the project, all or none.

        A template chosen twice in one request is copied once. The copies keep no
        link to their templates: choosing a template again later adds another task.

        Raises:
            UnknownTaskTemplateError: no template with some of the ids.
            ProjectNotFoundError: no such project, or the caller is not its member.
        """
        catalog = {template.id: template for template in self._templates.all()}
        chosen = [TaskTemplateId(template_id) for template_id in dict.fromkeys(data.template_ids)]
        unknown = [template_id for template_id in chosen if template_id not in catalog]
        if unknown:
            raise UnknownTaskTemplateError(unknown)
        project = await project_of_member(self._projects, data.project_id, data.user_id)

        now = utc_now()
        tasks = [Task.from_template(catalog[t], project_id=project.id) for t in chosen]
        for task, template_id in zip(tasks, chosen, strict=True):
            await self._tasks.add(task)
            await self._audit_log.add(
                AuditEntry.record(
                    project_id=project.id,
                    actor_id=UserId(data.user_id),
                    action=AuditAction.TASK_CREATED,
                    entity_id=task.id,
                    now=now,
                    details={"template_id": template_id},
                )
            )

        views = await self._read_model.get_many([task.id for task in tasks])
        await self._uow.commit()
        logger.info(
            "tasks_created_from_templates",
            extra={
                "project_id": str(project.id),
                "template_ids": chosen,
                "tasks": len(tasks),
                "user_id": str(data.user_id),
            },
        )
        return views
