import logging

from app.application.common.clock import utc_now
from app.application.common.membership import project_of_member
from app.application.common.patch import UNSET
from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.project_reader import AbstractProjectReader, ProjectDTO
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.projects.dto import UpdateProjectDTO
from app.application.projects.lookup import read_project_or_raise
from app.application.projects.services import MovingReminderScheduler
from app.domain.projects.value_objects import Address, ProjectName, TimeZone

logger = logging.getLogger(__name__)


class UpdateProject:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        reminders: MovingReminderScheduler,
        reader: AbstractProjectReader,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._reminders = reminders
        self._reader = reader
        self._uow = uow

    async def __call__(self, data: UpdateProjectDTO) -> ProjectDTO:
        """Change the given fields; any member may. A new moving date or time zone
        moves the pending moving date reminders.

        Raises:
            ProjectNotFoundError: no such project, or the user is not its member.
            DomainError: a new value is invalid.
        """
        project = await project_of_member(
            self._projects, data.project_id, data.user_id, for_update=True
        )
        schedule = (project.moving_date, project.time_zone)

        if data.name is not UNSET:
            project.rename(ProjectName(data.name))
        if data.moving_date is not UNSET:
            project.reschedule(data.moving_date)
        if data.time_zone is not UNSET:
            project.change_time_zone(TimeZone(data.time_zone))
        if data.qr_code_strategy is not UNSET:
            project.change_qr_code_strategy(data.qr_code_strategy)
        if data.origin_address is not UNSET or data.destination_address is not UNSET:
            project.change_addresses(
                origin=project.origin_address
                if data.origin_address is UNSET
                else _address(data.origin_address),
                destination=project.destination_address
                if data.destination_address is UNSET
                else _address(data.destination_address),
            )

        await self._projects.update(project)
        rescheduled = (project.moving_date, project.time_zone) != schedule
        if rescheduled:
            await self._reminders.reschedule_all(project, now=utc_now())
        await self._uow.commit()

        if rescheduled:
            logger.info(
                "project_rescheduled",
                extra={"project_id": str(project.id), "moving_date": str(project.moving_date)},
            )
        return await read_project_or_raise(self._reader, project.id, data.user_id)


def _address(value: str | None) -> Address | None:
    return None if value is None else Address(value)
