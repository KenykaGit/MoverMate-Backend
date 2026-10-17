from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.inventory.box import BoxStatus
from app.domain.inventory.value_objects import BOX_TITLE_MAX_LENGTH, QR_TOKEN_LENGTH
from app.infrastructure.db.base import Base
from app.infrastructure.db.constraints import ENUM_LENGTH, one_of

ACTIVE_BOX_NUMBER_UNIQUE_INDEX = "uq_boxes_project_id_number_active"
# Raised by the trigger that mirrors BOX_TRANSITIONS (see migration 0004).
BOX_STATUS_TRANSITION_CONSTRAINT = "ck_boxes_status_transition"


class BoxModel(Base):
    __tablename__ = "boxes"
    __table_args__ = (
        # Target of the composite foreign key that keeps items in their project.
        UniqueConstraint("project_id", "id", name="uq_boxes_project_id_id"),
        # A box may only go to a room of its own project.
        ForeignKeyConstraint(
            ["project_id", "destination_room_id"],
            ["rooms.project_id", "rooms.id"],
            name="fk_boxes_destination_room_id_rooms",
        ),
        # Predicates in the form Postgres prints them back (see the migrations test).
        Index(
            ACTIVE_BOX_NUMBER_UNIQUE_INDEX,
            "project_id",
            "number",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index(
            "ix_boxes_project_id_status_active",
            "project_id",
            "status",
            postgresql_where=text("is_active"),
        ),
        Index(
            "ix_boxes_project_id_destination_room_id_active",
            "project_id",
            "destination_room_id",
            postgresql_where=text("is_active"),
        ),
        CheckConstraint("number > 0", name="number_positive"),
        CheckConstraint(f"length(qr_token) = {QR_TOKEN_LENGTH}", name="qr_token_length"),
        CheckConstraint(one_of("status", BoxStatus), name="status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    number: Mapped[int]
    qr_token: Mapped[str] = mapped_column(String(32), unique=True)
    destination_room_id: Mapped[UUID]
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str | None] = mapped_column(String(BOX_TITLE_MAX_LENGTH))
    is_fragile: Mapped[bool]
    status: Mapped[str] = mapped_column(String(ENUM_LENGTH))
    status_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
