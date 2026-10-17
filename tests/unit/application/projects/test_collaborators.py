import pytest

from app.application.exceptions import ProjectNotFoundError, UserNotFoundError
from app.application.projects.add_collaborator import AddCollaborator
from app.application.projects.dto import (
    AddCollaboratorDTO,
    ProjectActionDTO,
    RemoveCollaboratorDTO,
    SetProjectRemindersDTO,
)
from app.application.projects.list_collaborators import ListCollaborators
from app.application.projects.remove_collaborator import RemoveCollaborator
from app.application.projects.services import MovingReminderScheduler
from app.application.projects.set_project_reminders import SetProjectReminders
from app.domain.projects.entity import Project
from app.domain.projects.errors import CannotRemoveOwnerError
from app.domain.users.entity import User
from app.domain.users.errors import InvalidUsernameError
from tests.factories import make_user
from tests.fakes import (
    FakeUnitOfWork,
    InMemoryNotificationRepository,
    InMemoryProjectReader,
    InMemoryProjectRepository,
    InMemoryUserRepository,
)


@pytest.fixture
def add(
    projects: InMemoryProjectRepository,
    users: InMemoryUserRepository,
    scheduler: MovingReminderScheduler,
    project_reader: InMemoryProjectReader,
    uow: FakeUnitOfWork,
) -> AddCollaborator:
    return AddCollaborator(projects, users, scheduler, project_reader, uow)


@pytest.fixture
def remove(
    projects: InMemoryProjectRepository,
    notifications: InMemoryNotificationRepository,
    uow: FakeUnitOfWork,
) -> RemoveCollaborator:
    return RemoveCollaborator(projects, notifications, uow)


@pytest.fixture
async def with_reminder(
    projects: InMemoryProjectRepository,
    notifications: InMemoryNotificationRepository,
    scheduler: MovingReminderScheduler,
    project_reader: InMemoryProjectReader,
    project: Project,
    owner: User,
) -> Project:
    """`project` with a reminder a week before the move, queued for both members."""
    await SetProjectReminders(projects, notifications, scheduler, project_reader, FakeUnitOfWork())(
        SetProjectRemindersDTO(project_id=project.id, user_id=owner.id, offsets_days=(7,))
    )
    return project


def add_dto(project: Project, by: User, username: str) -> AddCollaboratorDTO:
    return AddCollaboratorDTO(project_id=project.id, user_id=by.id, username=username)


def remove_dto(project: Project, by: User, who: User) -> RemoveCollaboratorDTO:
    return RemoveCollaboratorDTO(project_id=project.id, user_id=by.id, collaborator_id=who.id)


class TestList:
    @pytest.mark.usefixtures("stranger")
    async def test_owner_first_then_by_joining_and_deleted_accounts_hidden(
        self,
        add: AddCollaborator,
        projects: InMemoryProjectRepository,
        users: InMemoryUserRepository,
        project_reader: InMemoryProjectReader,
        project: Project,
        member: User,
    ) -> None:
        await add(add_dto(project, member, "stranger"))
        gone = make_user(username="gone")
        await users.add(gone)
        await add(add_dto(project, member, "gone"))
        gone.soft_delete()
        await users.update(gone)

        listed = await ListCollaborators(projects, project_reader)(
            ProjectActionDTO(project_id=project.id, user_id=member.id)
        )

        assert [(c.user.username, c.is_owner) for c in listed] == [
            ("owner", True),
            ("member", False),
            ("stranger", False),
        ]
        assert listed[1].created_at < listed[2].created_at

    async def test_strangers_get_not_found(
        self,
        projects: InMemoryProjectRepository,
        project_reader: InMemoryProjectReader,
        project: Project,
        stranger: User,
    ) -> None:
        with pytest.raises(ProjectNotFoundError):
            await ListCollaborators(projects, project_reader)(
                ProjectActionDTO(project_id=project.id, user_id=stranger.id)
            )


class TestAdd:
    async def test_by_username_with_moving_reminders(
        self,
        add: AddCollaborator,
        projects: InMemoryProjectRepository,
        notifications: InMemoryNotificationRepository,
        with_reminder: Project,
        member: User,
        stranger: User,
        uow: FakeUnitOfWork,
    ) -> None:
        added = await add(add_dto(with_reminder, member, "  Stranger "))

        assert added.is_new
        assert (added.collaborator.user.id, added.collaborator.is_owner) == (stranger.id, False)
        assert projects.projects[with_reminder.id].is_member(stranger.id)
        assert [n.user_id for n in notifications.pending()].count(stranger.id) == 1
        assert uow.commits == 1
        assert projects.locks >= 1

    async def test_again_is_a_no_op(
        self,
        add: AddCollaborator,
        notifications: InMemoryNotificationRepository,
        with_reminder: Project,
        owner: User,
        uow: FakeUnitOfWork,
    ) -> None:
        queued = len(notifications.notifications)

        added = await add(add_dto(with_reminder, owner, "member"))

        assert not added.is_new
        assert added.collaborator.user.username == "member"
        assert len(notifications.notifications) == queued
        assert uow.commits == 0

    async def test_unknown_or_deleted_user(
        self, add: AddCollaborator, users: InMemoryUserRepository, project: Project, owner: User
    ) -> None:
        await users.add(make_user(username="ghost", is_active=False))

        for username in ("nobody", "ghost"):
            with pytest.raises(UserNotFoundError):
                await add(add_dto(project, owner, username))

    async def test_malformed_username(
        self, add: AddCollaborator, project: Project, owner: User
    ) -> None:
        with pytest.raises(InvalidUsernameError):
            await add(add_dto(project, owner, "a"))

    async def test_strangers_cannot_add(
        self, add: AddCollaborator, project: Project, stranger: User
    ) -> None:
        with pytest.raises(ProjectNotFoundError):
            await add(add_dto(project, stranger, "stranger"))


class TestRemove:
    async def test_a_member_removes_another_and_their_notifications_go(
        self,
        remove: RemoveCollaborator,
        add: AddCollaborator,
        projects: InMemoryProjectRepository,
        notifications: InMemoryNotificationRepository,
        with_reminder: Project,
        owner: User,
        member: User,
        stranger: User,
    ) -> None:
        await add(add_dto(with_reminder, owner, "stranger"))

        await remove(remove_dto(with_reminder, member, stranger))

        assert not projects.projects[with_reminder.id].is_member(stranger.id)
        assert {n.user_id for n in notifications.pending()} == {owner.id, member.id}

    async def test_leaving_by_own_id(
        self,
        remove: RemoveCollaborator,
        projects: InMemoryProjectRepository,
        project: Project,
        member: User,
    ) -> None:
        await remove(remove_dto(project, member, member))

        assert not projects.projects[project.id].is_member(member.id)
        with pytest.raises(ProjectNotFoundError):  # no longer a member
            await remove(remove_dto(project, member, member))

    async def test_owner_cannot_be_removed(
        self,
        remove: RemoveCollaborator,
        projects: InMemoryProjectRepository,
        project: Project,
        owner: User,
        member: User,
        uow: FakeUnitOfWork,
    ) -> None:
        for by in (member, owner):
            with pytest.raises(CannotRemoveOwnerError):
                await remove(remove_dto(project, by, owner))
        assert projects.projects[project.id].is_member(owner.id)
        assert uow.commits == 0

    async def test_non_member_is_a_no_op(
        self,
        remove: RemoveCollaborator,
        project: Project,
        owner: User,
        stranger: User,
        uow: FakeUnitOfWork,
    ) -> None:
        await remove(remove_dto(project, owner, stranger))

        assert uow.commits == 0
