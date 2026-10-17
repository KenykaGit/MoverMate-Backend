"""project time zone, reminder on the moving day itself

Moving date reminders arrive at 09:00 where the move takes place, so a project
gets its IANA time zone. An offset of 0 days (the morning of the moving day)
becomes allowed (api-endpoints.md, section 4).

Expand only: the previous release never creates projects, and the widened CHECK
accepts every row it could write. The server default exists just for the backfill;
the domain always sets the time zone, so the default is dropped right after.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-17 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Literals, not imports from app: a migration must not change when the code does.
OFFSET_DAYS_CHECK = "ck_project_reminders_offset_days_range"


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("time_zone", sa.String(length=64), nullable=False, server_default="UTC"),
    )
    op.alter_column("projects", "time_zone", server_default=None)

    op.drop_constraint(op.f(OFFSET_DAYS_CHECK), "project_reminders", type_="check")
    op.create_check_constraint(
        op.f(OFFSET_DAYS_CHECK), "project_reminders", "offset_days BETWEEN 0 AND 365"
    )


def downgrade() -> None:
    # Reminders on the moving day cannot satisfy the old CHECK: they go (data loss, by design).
    op.execute(
        "UPDATE notifications SET status = 'CANCELLED' "
        "WHERE type = 'MOVING_DATE_REMINDER' AND reminder_offset_days = 0 AND status = 'PENDING'"
    )
    op.execute("DELETE FROM project_reminders WHERE offset_days = 0")
    op.drop_constraint(op.f(OFFSET_DAYS_CHECK), "project_reminders", type_="check")
    op.create_check_constraint(
        op.f(OFFSET_DAYS_CHECK), "project_reminders", "offset_days BETWEEN 1 AND 365"
    )

    op.drop_column("projects", "time_zone")
