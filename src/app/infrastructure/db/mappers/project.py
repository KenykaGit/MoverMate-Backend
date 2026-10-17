from app.domain.common.ids import ProjectId, UserId
from app.domain.common.reminders import MovingReminderOffset
from app.domain.projects.entity import Project
from app.domain.projects.value_objects import Address, ProjectName, QrCodeStrategy
from app.infrastructure.db.models.project import (
    ProjectCollaboratorModel,
    ProjectModel,
    ProjectReminderModel,
)


class ProjectMapper:
    """Converts between the domain Project (with its members) and its ORM rows."""

    @staticmethod
    def to_domain(model: ProjectModel) -> Project:
        return Project(
            id=ProjectId(model.id),
            owner_id=UserId(model.owner_id),
            name=ProjectName(model.name),
            moving_date=model.moving_date,
            origin_address=_address(model.origin_address),
            destination_address=_address(model.destination_address),
            qr_code_strategy=QrCodeStrategy(model.qr_code_strategy),
            member_ids={UserId(row.user_id) for row in model.collaborators},
            moving_reminders={MovingReminderOffset(row.offset_days) for row in model.reminders},
            is_active=model.is_active,
        )

    @staticmethod
    def to_model(project: Project) -> ProjectModel:
        return ProjectModel(
            id=project.id,
            owner_id=project.owner_id,
            name=project.name.value,
            moving_date=project.moving_date,
            origin_address=_text(project.origin_address),
            destination_address=_text(project.destination_address),
            qr_code_strategy=project.qr_code_strategy.value,
            is_active=project.is_active,
            # Sorted: a stable order of rows, whatever the set iteration order.
            collaborators=[
                ProjectCollaboratorModel(project_id=project.id, user_id=user_id)
                for user_id in sorted(project.member_ids)
            ],
            reminders=[
                ProjectReminderModel(project_id=project.id, offset_days=offset.days)
                for offset in sorted(project.moving_reminders)
            ],
        )


def _address(value: str | None) -> Address | None:
    return None if value is None else Address(value)


def _text(address: Address | None) -> str | None:
    return None if address is None else address.value
