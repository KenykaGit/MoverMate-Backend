"""drop users.email: accounts are identified by username only

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-16 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "uq_users_email_active", table_name="users", postgresql_where=sa.text("is_active")
    )
    op.drop_constraint(op.f("ck_users_email_lowercase"), "users", type_="check")
    op.drop_column("users", "email")


def downgrade() -> None:
    # The addresses are gone for good: restore the column with unique
    # placeholders (lowercase, as the CHECK requires) so the constraints hold.
    op.add_column("users", sa.Column("email", sa.String(length=254), nullable=True))
    op.execute("UPDATE users SET email = id::text || '@removed.invalid'")
    op.alter_column("users", "email", nullable=False)
    op.create_check_constraint(op.f("ck_users_email_lowercase"), "users", "email = lower(email)")
    op.create_index(
        "uq_users_email_active",
        "users",
        ["email"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
