from dishka import Provider, Scope, provide

from app.application.interfaces.task_templates import AbstractTaskTemplateCatalog
from app.infrastructure.templates.catalog import TASK_TEMPLATES_FILE, FileTaskTemplateCatalog


class TemplatesProvider(Provider):
    """Catalogs shipped with the release: read once per process."""

    scope = Scope.APP

    @provide
    def task_templates(self) -> AbstractTaskTemplateCatalog:
        return FileTaskTemplateCatalog(TASK_TEMPLATES_FILE)
