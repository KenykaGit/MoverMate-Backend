"""task phase, box title and fragility, moving date reminders

Aligns the schema with the frontend (frontend-backend-alignment.md): tasks get a
phase again, boxes a title and a fragile flag, projects reminders of the moving
date, and notifications the MOVING_DATE_REMINDER type.

Expand only: new columns are nullable or are backfilled, so the previous release
keeps working. NOT NULL columns get a server default just for the backfill; the
domain always sets them, so the default is dropped right after.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-17 12:55:46.831884
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Literals, not imports from app: a migration must not change when the code does.
OLD_TYPES = "('TASK_ASSIGNED', 'TASK_REMINDER', 'BOX_CONTENTS_REMINDER')"
NEW_TYPES = "('TASK_ASSIGNED', 'TASK_REMINDER', 'BOX_CONTENTS_REMINDER', 'MOVING_DATE_REMINDER')"
OLD_TARGET = (
    "(type = 'BOX_CONTENTS_REMINDER' AND box_id IS NOT NULL AND task_id IS NULL)"
    " OR (type IN ('TASK_ASSIGNED', 'TASK_REMINDER') AND task_id IS NOT NULL AND box_id IS NULL)"
)
NEW_TARGET = (
    f"{OLD_TARGET} OR (type = 'MOVING_DATE_REMINDER' AND task_id IS NULL AND box_id IS NULL)"
)
MOVING_DATE_PENDING = (
    "(((type)::text = 'MOVING_DATE_REMINDER'::text) AND ((status)::text = 'PENDING'::text))"
)


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("phase", sa.String(length=32), nullable=False, server_default="BEFORE"),
    )
    op.alter_column("tasks", "phase", server_default=None)
    op.create_check_constraint(
        op.f("ck_tasks_phase"), "tasks", "phase IN ('BEFORE', 'DURING', 'AFTER')"
    )
    op.create_index(
        "ix_tasks_project_id_phase_due_at_active",
        "tasks",
        ["project_id", "phase", "due_at"],
        unique=False,
        postgresql_where=sa.text("is_active"),
    )

    op.add_column("boxes", sa.Column("title", sa.String(length=200), nullable=True))
    op.add_column(
        "boxes",
        sa.Column("is_fragile", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("boxes", "is_fragile", server_default=None)

    op.create_table(
        "project_reminders",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("offset_days", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "offset_days BETWEEN 1 AND 365", name=op.f("ck_project_reminders_offset_days_range")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_project_reminders_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", "offset_days", name=op.f("pk_project_reminders")),
    )

    op.add_column("notifications", sa.Column("reminder_offset_days", sa.Integer(), nullable=True))
    # A new type widens two CHECKs: drop and create them again.
    op.drop_constraint(op.f("ck_notifications_type"), "notifications", type_="check")
    op.create_check_constraint(
        op.f("ck_notifications_type"), "notifications", f"type IN {NEW_TYPES}"
    )
    op.drop_constraint(op.f("ck_notifications_target_matches_type"), "notifications", type_="check")
    op.create_check_constraint(
        op.f("ck_notifications_target_matches_type"), "notifications", NEW_TARGET
    )
    op.create_check_constraint(
        op.f("ck_notifications_offset_days_iff_moving_date_reminder"),
        "notifications",
        "(reminder_offset_days IS NOT NULL) = (type = 'MOVING_DATE_REMINDER')",
    )
    op.create_index(
        "uq_notifications_moving_date_pending",
        "notifications",
        ["project_id", "user_id", "reminder_offset_days"],
        unique=True,
        postgresql_where=sa.text(MOVING_DATE_PENDING),
    )


def downgrade() -> None:
    # Rows of the new type cannot satisfy the old CHECKs: they go (data loss, by design).
    op.execute("DELETE FROM notifications WHERE type = 'MOVING_DATE_REMINDER'")
    op.drop_index(
        "uq_notifications_moving_date_pending",
        table_name="notifications",
        postgresql_where=sa.text(MOVING_DATE_PENDING),
    )
    op.drop_constraint(
        op.f("ck_notifications_offset_days_iff_moving_date_reminder"),
        "notifications",
        type_="check",
    )
    op.drop_constraint(op.f("ck_notifications_target_matches_type"), "notifications", type_="check")
    op.create_check_constraint(
        op.f("ck_notifications_target_matches_type"), "notifications", OLD_TARGET
    )
    op.drop_constraint(op.f("ck_notifications_type"), "notifications", type_="check")
    op.create_check_constraint(
        op.f("ck_notifications_type"), "notifications", f"type IN {OLD_TYPES}"
    )
    op.drop_column("notifications", "reminder_offset_days")

    op.drop_table("project_reminders")

    op.drop_column("boxes", "is_fragile")
    op.drop_column("boxes", "title")

    op.drop_index(
        "ix_tasks_project_id_phase_due_at_active",
        table_name="tasks",
        postgresql_where=sa.text("is_active"),
    )
    op.drop_constraint(op.f("ck_tasks_phase"), "tasks", type_="check")
    op.drop_column("tasks", "phase")
