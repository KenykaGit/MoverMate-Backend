from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.notification_context import (
    AbstractNotificationContextReader,
    BoxFacts,
    NotificationContext,
    TaskFacts,
)
from app.domain.notifications.notification import (
    BoxContentsReminder,
    Notification,
    TaskAssigned,
    TaskReminder,
)
from app.infrastructure.db.models.box import BoxModel
from app.infrastructure.db.models.project import ProjectCollaboratorModel, ProjectModel
from app.infrastructure.db.models.task import TaskModel
from app.infrastructure.db.models.user import UserModel

_C = ProjectCollaboratorModel


class SqlAlchemyNotificationContextReader(AbstractNotificationContextReader):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, notification: Notification) -> NotificationContext:
        is_member = (
            exists()
            .where(_C.project_id == ProjectModel.id, _C.user_id == notification.user_id)
            .label("is_member")
        )
        row = (
            await self._session.execute(
                select(
                    ProjectModel.name,
                    ProjectModel.time_zone,
                    ProjectModel.is_active,
                    UserModel.is_active.label("user_is_active"),
                    is_member,
                )
                .join(UserModel, UserModel.id == notification.user_id)
                .where(ProjectModel.id == notification.project_id)
            )
        ).one()  # both rows exist: notifications reference them by foreign keys
        return NotificationContext(
            project_name=row.name,
            project_time_zone=row.time_zone,
            project_is_active=row.is_active,
            recipient_is_active=row.user_is_active,
            recipient_is_member=row.is_member,
            task=await self._task(notification),
            box=await self._box(notification),
        )

    async def _task(self, notification: Notification) -> TaskFacts | None:
        match notification.target:
            case TaskAssigned(task_id=task_id) | TaskReminder(task_id=task_id):
                row = (
                    await self._session.execute(
                        select(
                            TaskModel.title,
                            TaskModel.due_at,
                            TaskModel.is_done,
                            TaskModel.is_active,
                        ).where(TaskModel.id == task_id)
                    )
                ).one()
                return TaskFacts(
                    title=row.title, due_at=row.due_at, is_done=row.is_done, is_active=row.is_active
                )
        return None

    async def _box(self, notification: Notification) -> BoxFacts | None:
        match notification.target:
            case BoxContentsReminder(box_id=box_id):
                row = (
                    await self._session.execute(
                        select(
                            BoxModel.number, BoxModel.status, BoxModel.qr_token, BoxModel.is_active
                        ).where(BoxModel.id == box_id)
                    )
                ).one()
                return BoxFacts(
                    number=row.number,
                    status=row.status,
                    qr_token=row.qr_token,
                    is_active=row.is_active,
                )
        return None
