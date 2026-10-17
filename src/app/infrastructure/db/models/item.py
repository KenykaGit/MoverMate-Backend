from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.inventory.item import ItemState
from app.domain.inventory.value_objects import ITEM_NAME_MAX_LENGTH, QUANTITY_MAX, Priority
from app.infrastructure.db.base import Base
from app.infrastructure.db.constraints import ENUM_LENGTH, one_of

# Raised by the trigger that mirrors ITEM_TRANSITIONS (see migration 0004).
ITEM_STATE_TRANSITION_CONSTRAINT = "ck_items_state_transition"


class ItemModel(Base):
    __tablename__ = "items"
    __table_args__ = (
        # An item may only refer to a box and a room of its own project.
        # MATCH SIMPLE: not checked while box_id is NULL.
        ForeignKeyConstraint(
            ["project_id", "box_id"],
            ["boxes.project_id", "boxes.id"],
            name="fk_items_box_id_boxes",
        ),
        ForeignKeyConstraint(
            ["project_id", "source_room_id"],
            ["rooms.project_id", "rooms.id"],
            name="fk_items_source_room_id_rooms",
        ),
        # Predicates in the form Postgres prints them back (see the migrations test).
        Index(
            "ix_items_name_fts",
            text("to_tsvector('russian'::regconfig, name)"),
            postgresql_using="gin",
            postgresql_where=text("is_active"),
        ),
        Index("ix_items_box_id", "box_id", postgresql_where=text("(box_id IS NOT NULL)")),
        Index(
            "ix_items_project_id_state_priority_active",
            "project_id",
            "state",
            "priority",
            postgresql_where=text("is_active"),
        ),
        Index(
            "ix_items_project_id_source_room_id_in_box",
            "project_id",
            "source_room_id",
            postgresql_where=text("(is_active AND ((state)::text = 'IN_BOX'::text))"),
        ),
        # The main invariant: a box_id exactly when the item is in a box.
        CheckConstraint(
            f"(state = '{ItemState.IN_BOX}') = (box_id IS NOT NULL)", name="box_id_iff_in_box"
        ),
        CheckConstraint(one_of("state", ItemState), name="state"),
        CheckConstraint(f"quantity BETWEEN 1 AND {QUANTITY_MAX}", name="quantity_range"),
        CheckConstraint(
            f"priority BETWEEN {min(Priority).value} AND {max(Priority).value}",
            name="priority_range",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    box_id: Mapped[UUID | None]
    source_room_id: Mapped[UUID]
    name: Mapped[str] = mapped_column(String(ITEM_NAME_MAX_LENGTH))
    quantity: Mapped[int]
    # A number, not a string: packing order sorts by it without CASE.
    priority: Mapped[int] = mapped_column(SmallInteger)
    state: Mapped[str] = mapped_column(String(ENUM_LENGTH))
    is_active: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
