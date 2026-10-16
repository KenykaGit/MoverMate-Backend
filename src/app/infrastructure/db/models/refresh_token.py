from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base

SHA256_HEX_LENGTH = 64


class RefreshTokenModel(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        # Only hashes are stored, never the tokens themselves.
        CheckConstraint(f"length(token_hash) = {SHA256_HEX_LENGTH}", name="token_hash_sha256"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # One session: a login and all its rotations. Reuse detection revokes by it.
    family_id: Mapped[UUID] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
