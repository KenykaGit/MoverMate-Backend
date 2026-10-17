from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.notifications.notification import NotificationStatus, NotificationType
from app.infrastructure.db.base import Base
from app.infrastructure.db.constraints import ENUM_LENGTH, one_of

# Raised by the trigger that mirrors NOTIFICATION_TRANSITIONS (see migration 0004).
NOTIFICATION_STATUS_TRANSITION_CONSTRAINT = "ck_notifications_status_transition"

_TASK = f"('{NotificationType.TASK_ASSIGNED}', '{NotificationType.TASK_REMINDER}')"


class NotificationModel(Base):
    """The outbox of push notifications; the worker polls it for due PENDING rows."""

    __tablename__ = "notifications"
    __table_args__ = (
        # The type decides which target columns are set (a sum type in the domain).
        CheckConstraint(
            f"(type = '{NotificationType.BOX_CONTENTS_REMINDER}'"
            " AND box_id IS NOT NULL AND task_id IS NULL)"
            f" OR (type IN {_TASK} AND task_id IS NOT NULL AND box_id IS NULL)",
            name="target_matches_type",
        ),
        CheckConstraint(
            f"(reminder_offset_hours IS NOT NULL) = (type = '{NotificationType.TASK_REMINDER}')",
            name="offset_iff_task_reminder",
        ),
        CheckConstraint(one_of("type", NotificationType), name="type"),
        CheckConstraint(one_of("status", NotificationStatus), name="status"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        # No duplicate pending reminders. Predicates in the form Postgres prints them back.
        Index(
            "uq_notifications_task_reminder_pending",
            "task_id",
            "user_id",
            "reminder_offset_hours",
            unique=True,
            postgresql_where=text(
                "(((type)::text = 'TASK_REMINDER'::text) AND ((status)::text = 'PENDING'::text))"
            ),
        ),
        Index(
            "uq_notifications_box_contents_pending",
            "box_id",
            "user_id",
            unique=True,
            postgresql_where=text(
                "(((type)::text = 'BOX_CONTENTS_REMINDER'::text)"
                " AND ((status)::text = 'PENDING'::text))"
            ),
        ),
        Index(
            "ix_notifications_scheduled_at_pending",
            "scheduled_at",
            postgresql_where=text("((status)::text = 'PENDING'::text)"),
        ),
        # Cancelling a member's pending notifications when they leave a project.
        Index(
            "ix_notifications_project_id_user_id_pending",
            "project_id",
            "user_id",
            postgresql_where=text("((status)::text = 'PENDING'::text)"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    type: Mapped[str] = mapped_column(String(ENUM_LENGTH))
    task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id"))
    box_id: Mapped[UUID | None] = mapped_column(ForeignKey("boxes.id"))
    reminder_offset_hours: Mapped[int | None]
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(ENUM_LENGTH))
    attempts: Mapped[int]
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
