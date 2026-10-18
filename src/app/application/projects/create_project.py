import logging

from app.application.common.clock import utc_now
from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.audit_log_repository import AbstractAuditLogRepository
from app.application.interfaces.project_reader import AbstractProjectReader, ProjectDTO
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.room_repository import AbstractRoomRepository
from app.application.projects.dto import CreateProjectDTO
from app.application.projects.lookup import read_project_or_raise
from app.application.projects.services import MovingReminderScheduler
from app.domain.audit.entity import AuditAction, AuditEntry
from app.domain.common.ids import UserId
from app.domain.common.reminders import MovingReminderOffset
from app.domain.inventory.room import Room
from app.domain.inventory.value_objects import RoomKind, RoomName
from app.domain.projects.entity import Project
from app.domain.projects.value_objects import Address, ProjectName, TimeZone

logger = logging.getLogger(__name__)


class CreateProject:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        rooms: AbstractRoomRepository,
        audit_log: AbstractAuditLogRepository,
        reminders: MovingReminderScheduler,
        reader: AbstractProjectReader,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._rooms = rooms
        self._audit_log = audit_log
        self._reminders = reminders
        self._reader = reader
        self._uow = uow

    async def __call__(self, data: CreateProjectDTO) -> ProjectDTO:
        """Create a project owned by `owner_id`, with its first rooms, in one transaction.

        Task templates are not copied: the user picks them later.

        Raises:
            DomainError: a name, address, time zone or reminder offset is invalid.
            RoomNameTakenError: two rooms of one kind have the same name.
        """
        owner_id = UserId(data.owner_id)
        project = Project.create(
            owner_id=owner_id,
            name=ProjectName(data.name),
            moving_date=data.moving_date,
            time_zone=TimeZone(data.time_zone),
            origin_address=_address(data.origin_address),
            destination_address=_address(data.destination_address),
            qr_code_strategy=data.qr_code_strategy,
        )
        project.replace_moving_reminders(
            MovingReminderOffset(days) for days in data.reminder_offsets_days
        )
        # Every value is checked before anything is written.
        rooms = [
            Room.create(project_id=project.id, kind=kind, name=RoomName(name))
            for kind, names in (
                (RoomKind.SOURCE, data.source_rooms),
                (RoomKind.DESTINATION, data.destination_rooms),
            )
            for name in names
        ]
        now = utc_now()

        await self._projects.add(project)
        for room in rooms:
            await self._rooms.add(room)
        await self._audit_log.add(
            AuditEntry.record(
                project_id=project.id,
                actor_id=owner_id,
                action=AuditAction.PROJECT_CREATED,
                entity_id=project.id,
                now=now,
            )
        )
        await self._reminders.schedule(
            project, members={owner_id}, offsets=project.moving_reminders, now=now
        )
        await self._uow.commit()

        logger.info(
            "project_created",
            extra={
                "project_id": str(project.id),
                "project_name": project.name.value,
                "user_id": str(owner_id),
                "rooms": len(rooms),
            },
        )
        return await read_project_or_raise(self._reader, project.id, owner_id)


def _address(value: str | None) -> Address | None:
    return None if value is None else Address(value)
