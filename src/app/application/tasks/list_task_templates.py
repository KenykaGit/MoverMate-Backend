from app.application.interfaces.task_templates import AbstractTaskTemplateCatalog
from app.application.tasks.dto import TaskTemplateDTO


class ListTaskTemplates:
    def __init__(self, templates: AbstractTaskTemplateCatalog) -> None:
        self._templates = templates

    async def __call__(self) -> list[TaskTemplateDTO]:
        """Every template, by phase of the move: the same for all users."""
        return [TaskTemplateDTO.from_entity(template) for template in self._templates.all()]
