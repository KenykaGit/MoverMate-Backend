"""A plan to work on: a project of `owner` with `member`, a `stranger` outside it and
a catalog of three templates. `preset.make(UseCase)` wires a use case to the
in-memory ports by the names of its constructor parameters."""

import inspect
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest

from app.application.common.clock import utc_now
from app.application.tasks.create_task import CreateTask
from app.application.tasks.dto import CreateTaskDTO, TaskDTO
from app.application.tasks.services import TaskNotifier
from app.domain.notifications.notification import TaskAssigned, TaskReminder
from app.domain.projects.entity import Project
from app.domain.tasks.value_objects import (
    TaskDescription,
    TaskPhase,
    TaskTemplate,
    TaskTemplateId,
    TaskTitle,
)
from app.domain.users.entity import User
from tests.factories import make_project, make_user
from tests.fakes import (
    FakeTaskReadModel,
    FakeTaskTemplateCatalog,
    FakeUnitOfWork,
    InMemoryAuditLogRepository,
    InMemoryNotificationRepository,
    InMemoryProjectRepository,
    InMemoryTaskRepository,
    InMemoryUserRepository,
)

TEMPLATES = (
    TaskTemplate(
        id=TaskTemplateId("book-movers"),
        title=TaskTitle("Book movers"),
        phase=TaskPhase.BEFORE,
        description=TaskDescription("Compare offers"),
    ),
    TaskTemplate(
        id=TaskTemplateId("photograph-meters"), title=TaskTitle("Meters"), phase=TaskPhase.DURING
    ),
    TaskTemplate(
        id=TaskTemplateId("check-boxes"), title=TaskTitle("Check boxes"), phase=TaskPhase.AFTER
    ),
)

type Queued = tuple[str, str, int | None]
"""A pending notification: (username, type, reminder offset in hours)."""


@dataclass(slots=True)
class Preset:
    owner: User
    member: User
    stranger: User
    project: Project
    users: InMemoryUserRepository
    projects: InMemoryProjectRepository
    tasks: InMemoryTaskRepository
    notifications: InMemoryNotificationRepository
    notifier: TaskNotifier
    templates: FakeTaskTemplateCatalog
    audit_log: InMemoryAuditLogRepository
    read_model: FakeTaskReadModel
    uow: FakeUnitOfWork

    def make[T](self, use_case: type[T]) -> T:
        parameters = inspect.signature(use_case).parameters
        ports: dict[str, Any] = {name: getattr(self, name) for name in parameters}
        return use_case(**ports)

    async def task(self, **overrides: Any) -> TaskDTO:
        """A task of the project created by the owner through the use case."""
        values: dict[str, Any] = {"title": "Order movers"} | overrides
        return await self.make(CreateTask)(
            CreateTaskDTO(user_id=self.owner.id, project_id=self.project.id, **values)
        )

    def queued(self) -> list[Queued]:
        """Pending notifications, in a stable order."""
        names = {user.id: user.username.value for user in self.users.users.values()}
        found = []
        for notification in self.notifications.pending():
            target = notification.target
            assert isinstance(target, TaskAssigned | TaskReminder)
            offset = target.offset.hours if isinstance(target, TaskReminder) else None
            found.append((names[notification.user_id], notification.type.value, offset))
        return sorted(found, key=lambda q: (q[0], q[1], q[2] or 0))


@pytest.fixture
def preset() -> Preset:
    return new_preset()


def new_preset() -> Preset:
    """A fresh plan: for table-driven tests that need clean state per case."""
    owner, member, stranger = (make_user(username=name) for name in ("owner", "member", "other"))
    project = make_project(owner_id=owner.id)
    project.add_member(member.id)
    users = InMemoryUserRepository([owner, member, stranger])
    projects = InMemoryProjectRepository([project])
    tasks = InMemoryTaskRepository(projects)
    notifications = InMemoryNotificationRepository()
    return Preset(
        owner=owner,
        member=member,
        stranger=stranger,
        project=project,
        users=users,
        projects=projects,
        tasks=tasks,
        notifications=notifications,
        notifier=TaskNotifier(notifications),
        templates=FakeTaskTemplateCatalog(TEMPLATES),
        audit_log=InMemoryAuditLogRepository(),
        read_model=FakeTaskReadModel(tasks, users),
        uow=FakeUnitOfWork(),
    )


def in_hours(hours: float) -> datetime:
    return utc_now() + timedelta(hours=hours)
