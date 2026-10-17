from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.application.common.patch import UNSET
from app.application.exceptions import ProjectNotFoundError
from app.application.projects.delete_project import DeleteProject
from app.application.projects.dto import (
    ProjectActionDTO,
    SetProjectRemindersDTO,
    UpdateProjectDTO,
)
from app.application.projects.services import MovingReminderScheduler
from app.application.projects.set_project_reminders import SetProjectReminders
from app.application.projects.update_project import UpdateProject
from app.domain.common.errors import InvalidMovingReminderOffsetError
from app.domain.common.reminders import MovingReminderOffset
from app.domain.notifications.notification import MovingDateReminder, NotificationStatus
from app.domain.projects.entity import Project
from app.domain.projects.errors import InvalidProjectNameError, NotProjectOwnerError
from app.domain.projects.value_objects import Address, QrCodeStrategy
from app.domain.users.entity import User
from tests.fakes import (
    FakeUnitOfWork,
    InMemoryNotificationRepository,
    InMemoryProjectReader,
    InMemoryProjectRepository,
)
from tests.unit.application.projects.conftest import in_days


@pytest.fixture
def set_reminders(
    projects: InMemoryProjectRepository,
    notifications: InMemoryNotificationRepository,
    scheduler: MovingReminderScheduler,
    project_reader: InMemoryProjectReader,
    uow: FakeUnitOfWork,
) -> SetProjectReminders:
    return SetProjectReminders(projects, notifications, scheduler, project_reader, uow)


def reminders_dto(project: Project, user: User, *days: int) -> SetProjectRemindersDTO:
    return SetProjectRemindersDTO(project_id=project.id, user_id=user.id, offsets_days=days)


class TestUpdateProject:
    @pytest.fixture
    def update(
        self,
        projects: InMemoryProjectRepository,
        scheduler: MovingReminderScheduler,
        project_reader: InMemoryProjectReader,
        uow: FakeUnitOfWork,
    ) -> UpdateProject:
        return UpdateProject(projects, scheduler, project_reader, uow)

    async def test_changes_only_the_given_fields(
        self,
        update: UpdateProject,
        projects: InMemoryProjectRepository,
        project: Project,
        member: User,
    ) -> None:
        stored = projects.projects[project.id]
        stored.change_addresses(origin=Address("Old street"), destination=Address("New street"))
        await projects.update(stored)

        updated = await update(
            UpdateProjectDTO(
                project_id=project.id,
                user_id=member.id,  # any member may
                name=" Renamed ",
                destination_address=None,
                qr_code_strategy=QrCodeStrategy.OPEN,
            )
        )

        assert (updated.name, updated.origin_address, updated.destination_address) == (
            "Renamed",
            "Old street",
            None,
        )
        assert updated.qr_code_strategy is QrCodeStrategy.OPEN
        assert (updated.moving_date, updated.time_zone) == (project.moving_date, "Europe/Moscow")
        assert projects.locks == 1

    async def test_empty_patch_changes_nothing(
        self, update: UpdateProject, project: Project, member: User
    ) -> None:
        updated = await update(UpdateProjectDTO(project_id=project.id, user_id=member.id))

        assert (updated.name, updated.moving_date) == (project.name.value, project.moving_date)

    async def test_new_moving_date_or_time_zone_moves_pending_reminders(
        self,
        update: UpdateProject,
        set_reminders: SetProjectReminders,
        notifications: InMemoryNotificationRepository,
        project: Project,
        owner: User,
    ) -> None:
        await set_reminders(reminders_dto(project, owner, 1))
        new_date = in_days(40)

        await update(
            UpdateProjectDTO(
                project_id=project.id,
                user_id=owner.id,
                moving_date=new_date,
                time_zone="Asia/Vladivostok",  # UTC+10
            )
        )

        pending = notifications.pending()
        day = in_days(39)  # one day before the new date, 09:00 in Vladivostok
        local = datetime(day.year, day.month, day.day, 9, tzinfo=ZoneInfo("Asia/Vladivostok"))
        assert {n.scheduled_at for n in pending} == {local.astimezone(UTC)}
        assert len(pending) == 2  # owner and member
        assert (
            sum(n.status is NotificationStatus.CANCELLED for n in notifications.notifications) == 2
        )

    async def test_invalid_value_is_rejected_before_writing(
        self, update: UpdateProject, project: Project, owner: User, uow: FakeUnitOfWork
    ) -> None:
        with pytest.raises(InvalidProjectNameError):
            await update(UpdateProjectDTO(project_id=project.id, user_id=owner.id, name=" "))
        assert uow.commits == 0

    async def test_strangers_get_not_found(
        self, update: UpdateProject, project: Project, stranger: User
    ) -> None:
        with pytest.raises(ProjectNotFoundError):
            await update(UpdateProjectDTO(project_id=project.id, user_id=stranger.id, name=UNSET))


class TestDeleteProject:
    @pytest.fixture
    def delete(
        self,
        projects: InMemoryProjectRepository,
        notifications: InMemoryNotificationRepository,
        uow: FakeUnitOfWork,
    ) -> DeleteProject:
        return DeleteProject(projects, notifications, uow)

    async def test_owner_deletes_and_pending_notifications_are_cancelled(
        self,
        delete: DeleteProject,
        set_reminders: SetProjectReminders,
        projects: InMemoryProjectRepository,
        notifications: InMemoryNotificationRepository,
        project: Project,
        owner: User,
    ) -> None:
        await set_reminders(reminders_dto(project, owner, 7))

        await delete(ProjectActionDTO(project_id=project.id, user_id=owner.id))

        assert projects.projects[project.id].is_active is False
        assert notifications.pending() == []
        with pytest.raises(ProjectNotFoundError):  # gone for everybody, also a second time
            await delete(ProjectActionDTO(project_id=project.id, user_id=owner.id))

    async def test_other_members_may_not(
        self,
        delete: DeleteProject,
        projects: InMemoryProjectRepository,
        project: Project,
        member: User,
        uow: FakeUnitOfWork,
    ) -> None:
        with pytest.raises(NotProjectOwnerError):
            await delete(ProjectActionDTO(project_id=project.id, user_id=member.id))
        assert projects.projects[project.id].is_active
        assert uow.commits == 0

    async def test_strangers_get_not_found(
        self, delete: DeleteProject, project: Project, stranger: User
    ) -> None:
        with pytest.raises(ProjectNotFoundError):
            await delete(ProjectActionDTO(project_id=project.id, user_id=stranger.id))


class TestSetProjectReminders:
    async def test_queues_added_offsets_for_every_member(
        self,
        set_reminders: SetProjectReminders,
        notifications: InMemoryNotificationRepository,
        project: Project,
        owner: User,
        member: User,
    ) -> None:
        result = await set_reminders(reminders_dto(project, member, 7, 0, 7))

        assert result.reminder_offsets_days == (0, 7)
        assert {(n.user_id, n.target) for n in notifications.pending()} == {
            (user.id, MovingDateReminder(MovingReminderOffset(days)))
            for user in (owner, member)
            for days in (0, 7)
        }

    async def test_removed_offsets_cancel_their_notifications_only(
        self,
        set_reminders: SetProjectReminders,
        notifications: InMemoryNotificationRepository,
        project: Project,
        owner: User,
    ) -> None:
        await set_reminders(reminders_dto(project, owner, 7, 1))

        result = await set_reminders(reminders_dto(project, owner, 1, 3))

        assert result.reminder_offsets_days == (1, 3)
        assert {n.target for n in notifications.pending()} == {
            MovingDateReminder(MovingReminderOffset(1)),
            MovingDateReminder(MovingReminderOffset(3)),
        }
        assert len(notifications.pending()) == 4

    async def test_same_set_again_writes_nothing(
        self,
        set_reminders: SetProjectReminders,
        notifications: InMemoryNotificationRepository,
        project: Project,
        owner: User,
        uow: FakeUnitOfWork,
    ) -> None:
        await set_reminders(reminders_dto(project, owner, 7))
        queued = len(notifications.notifications)

        await set_reminders(reminders_dto(project, owner, 7, 7))

        assert len(notifications.notifications) == queued
        assert uow.commits == 1

    async def test_out_of_range(
        self, set_reminders: SetProjectReminders, project: Project, owner: User
    ) -> None:
        for days in (-1, 366):
            with pytest.raises(InvalidMovingReminderOffsetError):
                await set_reminders(reminders_dto(project, owner, days))

    async def test_strangers_get_not_found(
        self, set_reminders: SetProjectReminders, project: Project, stranger: User
    ) -> None:
        with pytest.raises(ProjectNotFoundError):
            await set_reminders(reminders_dto(project, stranger, 1))
