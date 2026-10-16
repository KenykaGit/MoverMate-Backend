from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.users.value_objects import USERNAME_MAX_LENGTH
from app.infrastructure.db.base import Base

# Unique among active users only: a soft-deleted user frees its username.
ACTIVE_USERNAME_UNIQUE_INDEX = "uq_users_username_active"


class UserModel(Base):
    __tablename__ = "users"
    __table_args__ = (
        # Predicates in the form Postgres prints them back (see the migrations test).
        Index(
            ACTIVE_USERNAME_UNIQUE_INDEX,
            "username",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        # Value objects normalize to lowercase; the database guards that too.
        CheckConstraint("username = lower(username)", name="username_lowercase"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(USERNAME_MAX_LENGTH))
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
