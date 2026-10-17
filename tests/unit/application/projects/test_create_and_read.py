from datetime import UTC, datetime

import pytest

from app.application.exceptions import ProjectNotFoundError, RoomNameTakenError
from app.application.projects.create_project import CreateProject
from app.application.projects.dto import CreateProjectDTO, ProjectActionDTO
from app.application.projects.get_project import GetProject
from app.application.projects.list_projects import ListProjects
from app.application.projects.services import MovingReminderScheduler
from app.domain.audit.entity import AuditAction
from app.domain.common.errors import InvalidMovingReminderOffsetError
from app.domain.common.ids import new_id
from app.domain.common.reminders import MovingReminderOffset
from app.domain.inventory.value_objects import RoomKind
from app.domain.notifications.notification import MovingDateReminder
from app.domain.projects.entity import Project
from app.domain.projects.errors import InvalidTimeZoneError
from app.domain.projects.value_objects import QrCodeStrategy
from app.domain.users.entity import User
from tests.factories import make_project
from tests.fakes import (
    FakeUnitOfWork,
    InMemoryAuditLogRepository,
    InMemoryNotificationRepository,
    InMemoryProjectReader,
    InMemoryProjectRepository,
    InMemoryRoomRepository,
)
from tests.unit.application.projects.conftest import in_days


class TestCreateProject:
    @pytest.fixture
    def create(
        self,
        projects: InMemoryProjectRepository,
        rooms: InMemoryRoomRepository,
        audit_log: InMemoryAuditLogRepository,
        scheduler: MovingReminderScheduler,
        project_reader: InMemoryProjectReader,
        uow: FakeUnitOfWork,
    ) -> CreateProject:
        return CreateProject(projects, rooms, audit_log, scheduler, project_reader, uow)

    def dto(self, owner: User, **overrides: object) -> CreateProjectDTO:
        values: dict[str, object] = {
            "owner_id": owner.id,
            "name": "  Переезд на Садовую ",
            "moving_date": in_days(30),
            "time_zone": "Europe/Moscow",
        }
        return CreateProjectDTO(**(values | overrides))  # type: ignore[arg-type]

    async def test_owner_is_the_first_member_and_sees_the_project(
        self,
        create: CreateProject,
        owner: User,
        projects: InMemoryProjectRepository,
        audit_log: InMemoryAuditLogRepository,
        uow: FakeUnitOfWork,
    ) -> None:
        created = await create(self.dto(owner, origin_address="ул. Ленина, 14"))

        stored = projects.projects[created.id]
        assert stored.member_ids == {owner.id}
        assert (created.name, created.origin_address) == ("Переезд на Садовую", "ул. Ленина, 14")
        assert (created.owner.username, created.is_owner) == ("owner", True)
        assert created.qr_code_strategy is QrCodeStrategy.PROTECTED
        assert created.reminder_offsets_days == ()
        assert [e.action for e in audit_log.entries] == [AuditAction.PROJECT_CREATED]
        assert uow.commits == 1

    async def test_rooms_are_created_with_the_project(
        self, create: CreateProject, owner: User, rooms: InMemoryRoomRepository
    ) -> None:
        created = await create(
            self.dto(owner, source_rooms=("Кухня",), destination_rooms=("Кухня", "Детская"))
        )

        assert sorted((r.kind, r.name.value) for r in rooms.rooms.values()) == [
            (RoomKind.DESTINATION, "Детская"),
            (RoomKind.DESTINATION, "Кухня"),
            (RoomKind.SOURCE, "Кухня"),
        ]
        assert {r.project_id for r in rooms.rooms.values()} == {created.id}

    async def test_duplicate_room_names_of_one_kind_abort(
        self, create: CreateProject, owner: User, uow: FakeUnitOfWork
    ) -> None:
        with pytest.raises(RoomNameTakenError):
            await create(self.dto(owner, source_rooms=("Кухня", " Кухня")))
        assert uow.commits == 0

    async def test_reminders_are_queued_for_the_owner_at_nine_local_time(
        self,
        create: CreateProject,
        owner: User,
        notifications: InMemoryNotificationRepository,
    ) -> None:
        moving_date = in_days(30)
        created = await create(
            self.dto(owner, moving_date=moving_date, reminder_offsets_days=(7, 0, 7))
        )

        assert created.reminder_offsets_days == (0, 7)
        queued = sorted(notifications.pending(), key=lambda n: n.scheduled_at)
        assert [n.user_id for n in queued] == [owner.id, owner.id]
        assert [n.target for n in queued] == [
            MovingDateReminder(MovingReminderOffset(7)),
            MovingDateReminder(MovingReminderOffset(0)),
        ]
        # Moscow is UTC+3 all year: 09:00 local is 06:00 UTC.
        assert queued[-1].scheduled_at == datetime(
            moving_date.year, moving_date.month, moving_date.day, 6, tzinfo=UTC
        )

    async def test_reminders_already_past_are_not_queued(
        self, create: CreateProject, owner: User, notifications: InMemoryNotificationRepository
    ) -> None:
        created = await create(
            self.dto(owner, moving_date=in_days(3), reminder_offsets_days=(1, 30))
        )

        assert created.reminder_offsets_days == (1, 30)  # kept, just nothing to send
        assert [n.target for n in notifications.pending()] == [
            MovingDateReminder(MovingReminderOffset(1))
        ]

    @pytest.mark.parametrize(
        ("overrides", "error"),
        [
            ({"time_zone": "Mars/Olympus"}, InvalidTimeZoneError),
            ({"time_zone": "europe/moscow"}, InvalidTimeZoneError),
            ({"reminder_offsets_days": (366,)}, InvalidMovingReminderOffsetError),
            ({"reminder_offsets_days": (-1,)}, InvalidMovingReminderOffsetError),
        ],
    )
    async def test_invalid_values_write_nothing(
        self,
        create: CreateProject,
        owner: User,
        projects: InMemoryProjectRepository,
        overrides: dict[str, object],
        error: type[Exception],
    ) -> None:
        with pytest.raises(error):
            await create(self.dto(owner, **overrides))
        assert projects.projects == {}


class TestReading:
    async def test_list_shows_active_projects_of_the_member_by_moving_date(
        self,
        projects: InMemoryProjectRepository,
        project_reader: InMemoryProjectReader,
        owner: User,
        member: User,
    ) -> None:
        later = make_project(owner_id=member.id, moving_date=in_days(60))
        later.add_member(owner.id)
        sooner = make_project(owner_id=owner.id, moving_date=in_days(10))
        deleted = make_project(owner_id=owner.id, moving_date=in_days(5))
        deleted.soft_delete(by=owner.id)
        foreign = make_project(owner_id=member.id)
        for p in (later, sooner, deleted, foreign):
            await projects.add(p)

        listed = await ListProjects(project_reader)(owner.id)

        assert [(p.id, p.is_owner) for p in listed] == [(sooner.id, True), (later.id, False)]

    async def test_get_hides_the_project_from_strangers(
        self,
        project_reader: InMemoryProjectReader,
        project: Project,
        member: User,
        stranger: User,
    ) -> None:
        get = GetProject(project_reader)
        project_id = project.id

        assert (await get(ProjectActionDTO(project_id=project_id, user_id=member.id))).id == (
            project_id
        )
        for user_id, target in ((stranger.id, project_id), (member.id, new_id())):
            with pytest.raises(ProjectNotFoundError):
                await get(ProjectActionDTO(project_id=target, user_id=user_id))
