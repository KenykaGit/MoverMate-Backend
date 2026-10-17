from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.notifications.push_subscription import ENDPOINT_MAX_LENGTH, KEY_MAX_LENGTH
from app.infrastructure.db.base import Base


class PushSubscriptionModel(Base):
    __tablename__ = "push_subscriptions"
    __table_args__ = (
        Index(
            "ix_push_subscriptions_user_id_active", "user_id", postgresql_where=text("is_active")
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    # One browser installation: re-subscribing under another user moves the row (upsert).
    endpoint: Mapped[str] = mapped_column(String(ENDPOINT_MAX_LENGTH), unique=True)
    p256dh: Mapped[str] = mapped_column(String(KEY_MAX_LENGTH))
    auth: Mapped[str] = mapped_column(String(KEY_MAX_LENGTH))
    is_active: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
