"""All ORM models, so that importing this package fills Base.metadata (Alembic)."""

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.audit_log import AuditLogModel
from app.infrastructure.db.models.box import BoxModel
from app.infrastructure.db.models.item import ItemModel
from app.infrastructure.db.models.notification import NotificationModel
from app.infrastructure.db.models.project import ProjectCollaboratorModel, ProjectModel
from app.infrastructure.db.models.push_subscription import PushSubscriptionModel
from app.infrastructure.db.models.refresh_token import RefreshTokenModel
from app.infrastructure.db.models.room import RoomModel
from app.infrastructure.db.models.task import TaskCollaboratorModel, TaskModel, TaskReminderModel
from app.infrastructure.db.models.user import UserModel

__all__ = [
    "AuditLogModel",
    "Base",
    "BoxModel",
    "ItemModel",
    "NotificationModel",
    "ProjectCollaboratorModel",
    "ProjectModel",
    "PushSubscriptionModel",
    "RefreshTokenModel",
    "RoomModel",
    "TaskCollaboratorModel",
    "TaskModel",
    "TaskReminderModel",
    "UserModel",
]
