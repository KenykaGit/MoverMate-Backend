from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    PrimaryKeyConstraint,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.common.reminders import MOVING_REMINDER_MAX_DAYS, MOVING_REMINDER_MIN_DAYS
from app.domain.projects.value_objects import (
    ADDRESS_MAX_LENGTH,
    PROJECT_NAME_MAX_LENGTH,
    TIME_ZONE_MAX_LENGTH,
    QrCodeStrategy,
)
from app.infrastructure.db.base import Base
from app.infrastructure.db.constraints import ENUM_LENGTH, one_of


class ProjectModel(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(one_of("qr_code_strategy", QrCodeStrategy), name="qr_code_strategy"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(PROJECT_NAME_MAX_LENGTH))
    moving_date: Mapped[date] = mapped_column(Date)
    origin_address: Mapped[str | None] = mapped_column(String(ADDRESS_MAX_LENGTH))
    destination_address: Mapped[str | None] = mapped_column(String(ADDRESS_MAX_LENGTH))
    qr_code_strategy: Mapped[str] = mapped_column(String(ENUM_LENGTH))
    time_zone: Mapped[str] = mapped_column(String(TIME_ZONE_MAX_LENGTH))
    is_active: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Parts of the aggregate: loaded and saved together with the project.
    collaborators: Mapped[list[ProjectCollaboratorModel]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )
    reminders: Mapped[list[ProjectReminderModel]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class ProjectCollaboratorModel(Base):
    """A member of a project. Rows are deleted physically, cascading to task assignments."""

    __tablename__ = "project_collaborators"
    __table_args__ = (PrimaryKeyConstraint("project_id", "user_id"),)

    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectReminderModel(Base):
    """A moving date reminder: every member is notified this many days before the move."""

    __tablename__ = "project_reminders"
    __table_args__ = (
        PrimaryKeyConstraint("project_id", "offset_days"),
        CheckConstraint(
            f"offset_days BETWEEN {MOVING_REMINDER_MIN_DAYS} AND {MOVING_REMINDER_MAX_DAYS}",
            name="offset_days_range",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    offset_days: Mapped[int]
