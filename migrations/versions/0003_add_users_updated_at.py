"""add users.updated_at: last change of the row, maintained by the database and the ORM

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-17 10:59:40.306344
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Expand only: the previous release does not write the column, the server
    # default fills it. Existing rows get the migration time.
    op.add_column(
        "users",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "updated_at")
