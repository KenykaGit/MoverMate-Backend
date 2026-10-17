from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.inventory.value_objects import ROOM_NAME_MAX_LENGTH, RoomKind
from app.infrastructure.db.base import Base
from app.infrastructure.db.constraints import ENUM_LENGTH, one_of

ACTIVE_ROOM_NAME_UNIQUE_INDEX = "uq_rooms_project_id_kind_name_active"


class RoomModel(Base):
    __tablename__ = "rooms"
    __table_args__ = (
        # Target of the composite foreign keys that keep boxes and items in their project.
        UniqueConstraint("project_id", "id", name="uq_rooms_project_id_id"),
        Index(
            ACTIVE_ROOM_NAME_UNIQUE_INDEX,
            "project_id",
            "kind",
            "name",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        CheckConstraint(one_of("kind", RoomKind), name="kind"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    kind: Mapped[str] = mapped_column(String(ENUM_LENGTH))
    name: Mapped[str] = mapped_column(String(ROOM_NAME_MAX_LENGTH))
    is_active: Mapped[bool]
