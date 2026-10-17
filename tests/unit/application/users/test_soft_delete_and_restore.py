import pytest

from app.application.exceptions import (
    UsernameAlreadyTakenError,
    UserNotFoundError,
    UserOwnsProjectsError,
)
from app.application.users.restore_user import RestoreUser
from app.application.users.soft_delete_user import SoftDeleteUser
from tests.factories import make_project, make_refresh_token, make_user
from tests.fakes import (
    FakeUnitOfWork,
    InMemoryProjectRepository,
    InMemoryRefreshTokenRepository,
    InMemoryUserRepository,
)


class TestSoftDelete:
    @pytest.fixture
    def delete(
        self,
        users: InMemoryUserRepository,
        refresh_tokens: InMemoryRefreshTokenRepository,
        projects: InMemoryProjectRepository,
        uow: FakeUnitOfWork,
    ) -> SoftDeleteUser:
        return SoftDeleteUser(users, refresh_tokens, projects, uow)

    async def test_deactivates_and_ends_every_session(
        self,
        delete: SoftDeleteUser,
        users: InMemoryUserRepository,
        refresh_tokens: InMemoryRefreshTokenRepository,
        uow: FakeUnitOfWork,
    ) -> None:
        user = make_user()
        await users.add(user)
        await refresh_tokens.add(make_refresh_token(user.id))

        await delete(user.id)

        assert users.users[user.id].is_active is False  # the data is kept
        assert refresh_tokens.active_for(user.id) == []
        assert uow.commits == 1

    async def test_is_idempotent(
        self, delete: SoftDeleteUser, users: InMemoryUserRepository, uow: FakeUnitOfWork
    ) -> None:
        user = make_user()
        await users.add(user)

        await delete(user.id)
        await delete(user.id)
        await delete(make_user().id)  # never existed

        assert uow.commits == 1

    async def test_owner_of_active_projects_must_delete_them_first(
        self,
        delete: SoftDeleteUser,
        users: InMemoryUserRepository,
        refresh_tokens: InMemoryRefreshTokenRepository,
        projects: InMemoryProjectRepository,
        uow: FakeUnitOfWork,
    ) -> None:
        user = make_user()
        await users.add(user)
        await refresh_tokens.add(make_refresh_token(user.id))
        project = make_project(owner_id=user.id)
        await projects.add(project)

        with pytest.raises(UserOwnsProjectsError):
            await delete(user.id)
        assert users.users[user.id].is_active
        assert len(refresh_tokens.active_for(user.id)) == 1
        assert uow.commits == 0

        project.soft_delete(by=user.id)
        await projects.update(project)
        await delete(user.id)
        assert users.users[user.id].is_active is False

    async def test_membership_in_other_projects_does_not_block(
        self,
        delete: SoftDeleteUser,
        users: InMemoryUserRepository,
        projects: InMemoryProjectRepository,
    ) -> None:
        user = make_user()
        await users.add(user)
        project = make_project()
        project.add_member(user.id)
        await projects.add(project)

        await delete(user.id)

        assert users.users[user.id].is_active is False
        assert projects.projects[project.id].is_member(user.id)  # kept for a restore


class TestRestore:
    @pytest.fixture
    def restore(self, users: InMemoryUserRepository, uow: FakeUnitOfWork) -> RestoreUser:
        return RestoreUser(users, uow)

    async def test_restores_deleted_user(
        self, restore: RestoreUser, users: InMemoryUserRepository, uow: FakeUnitOfWork
    ) -> None:
        user = make_user(is_active=False)
        await users.add(user)

        result = await restore(user.id)

        assert result.id == user.id
        assert users.users[user.id].is_active
        assert uow.commits == 1

    async def test_active_user_is_left_alone(
        self, restore: RestoreUser, users: InMemoryUserRepository, uow: FakeUnitOfWork
    ) -> None:
        user = make_user()
        await users.add(user)

        await restore(user.id)

        assert uow.commits == 0

    async def test_unknown_user(self, restore: RestoreUser) -> None:
        with pytest.raises(UserNotFoundError):
            await restore(make_user().id)

    async def test_fails_if_username_was_taken_meanwhile(
        self, restore: RestoreUser, users: InMemoryUserRepository
    ) -> None:
        deleted = make_user(is_active=False)
        await users.add(deleted)
        await users.add(make_user())  # same username, now active

        with pytest.raises(UsernameAlreadyTakenError):
            await restore(deleted.id)
        assert users.users[deleted.id].is_active is False
