from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.common.reminders import REMINDER_OFFSET_MAX_HOURS, REMINDER_OFFSET_MIN_HOURS
from app.domain.tasks.value_objects import (
    TASK_DESCRIPTION_MAX_LENGTH,
    TASK_TITLE_MAX_LENGTH,
    TaskPhase,
)
from app.infrastructure.db.base import Base
from app.infrastructure.db.constraints import ENUM_LENGTH, one_of


class TaskModel(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        # Target of the composite foreign key that keeps assignees in the task's project.
        UniqueConstraint("id", "project_id", name="uq_tasks_id_project_id"),
        Index(
            "ix_tasks_project_id_due_at_active",
            "project_id",
            "due_at",
            postgresql_where=text("is_active"),
        ),
        # The plan screen: one column per phase, ordered by due time.
        Index(
            "ix_tasks_project_id_phase_due_at_active",
            "project_id",
            "phase",
            "due_at",
            postgresql_where=text("is_active"),
        ),
        CheckConstraint("is_done = (done_at IS NOT NULL)", name="done_at_iff_done"),
        CheckConstraint(one_of("phase", TaskPhase), name="phase"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    title: Mapped[str] = mapped_column(String(TASK_TITLE_MAX_LENGTH))
    description: Mapped[str | None] = mapped_column(String(TASK_DESCRIPTION_MAX_LENGTH))
    phase: Mapped[str] = mapped_column(String(ENUM_LENGTH))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_done: Mapped[bool]
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Parts of the aggregate: loaded and saved together with the task.
    assignees: Mapped[list[TaskCollaboratorModel]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )
    reminders: Mapped[list[TaskReminderModel]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class TaskCollaboratorModel(Base):
    """An assignee of a task: always a member of the task's project."""

    __tablename__ = "task_collaborators"
    __table_args__ = (
        PrimaryKeyConstraint("task_id", "user_id"),
        ForeignKeyConstraint(
            ["task_id", "project_id"],
            ["tasks.id", "tasks.project_id"],
            name="fk_task_collaborators_task_id_tasks",
            ondelete="CASCADE",
        ),
        # Removing a member from the project removes their assignments.
        ForeignKeyConstraint(
            ["project_id", "user_id"],
            ["project_collaborators.project_id", "project_collaborators.user_id"],
            name="fk_task_collaborators_member_project_collaborators",
            ondelete="CASCADE",
        ),
        Index("ix_task_collaborators_project_id_user_id", "project_id", "user_id"),
    )

    task_id: Mapped[UUID]
    project_id: Mapped[UUID]
    user_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TaskReminderModel(Base):
    __tablename__ = "task_reminders"
    __table_args__ = (
        PrimaryKeyConstraint("task_id", "offset_hours"),
        CheckConstraint(
            f"offset_hours BETWEEN {REMINDER_OFFSET_MIN_HOURS} AND {REMINDER_OFFSET_MAX_HOURS}",
            name="offset_hours_range",
        ),
    )

    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    offset_hours: Mapped[int]
