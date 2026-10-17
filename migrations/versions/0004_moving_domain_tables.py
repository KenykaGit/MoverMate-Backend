"""moving domain tables: projects, rooms, boxes, items, tasks, notifications, audit log

Tables, constraints and indexes follow docs/db-design.md. The state machines of
the domain (BOX_TRANSITIONS, ITEM_TRANSITIONS, NOTIFICATION_TRANSITIONS) are
copied into triggers; tests/integration/migrations checks both agree.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-17 11:53:16.622709
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Literals, not imports from app: a migration must not change when the code does.
# Violations are raised as check_violation with a constraint name, so repositories
# recognize them like any CHECK.
TRIGGERS = (
    """
    CREATE FUNCTION boxes_check_status_transition() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
        statuses CONSTANT text[] := ARRAY['PACKING', 'PACKED', 'LOADED', 'DELIVERED', 'UNPACKED'];
    BEGIN
        -- One step forward or back.
        IF abs(array_position(statuses, NEW.status::text)
               - array_position(statuses, OLD.status::text)) <> 1 THEN
            RAISE EXCEPTION 'box % cannot go from % to %', OLD.id, OLD.status, NEW.status
                USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_boxes_status_transition';
        END IF;
        RETURN NEW;
    END
    $$
    """,
    """
    CREATE TRIGGER boxes_status_transition
    BEFORE UPDATE OF status ON boxes
    FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status)
    EXECUTE FUNCTION boxes_check_status_transition()
    """,
    """
    CREATE FUNCTION items_check_state_transition() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF OLD.state::text || '>' || NEW.state::text <> ALL (ARRAY[
            'TO_PACK>IN_BOX', 'IN_BOX>TO_PACK', 'IN_BOX>TAKEN_OUT', 'TAKEN_OUT>IN_BOX',
            'TO_PACK>LOST', 'IN_BOX>LOST', 'TAKEN_OUT>LOST',
            'LOST>TO_PACK', 'LOST>IN_BOX', 'LOST>TAKEN_OUT'
        ]) THEN
            RAISE EXCEPTION 'item % cannot go from % to %', OLD.id, OLD.state, NEW.state
                USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_items_state_transition';
        END IF;
        RETURN NEW;
    END
    $$
    """,
    # Moving between boxes keeps the state (IN_BOX -> IN_BOX): no check needed.
    """
    CREATE TRIGGER items_state_transition
    BEFORE UPDATE OF state ON items
    FOR EACH ROW WHEN (OLD.state IS DISTINCT FROM NEW.state)
    EXECUTE FUNCTION items_check_state_transition()
    """,
    """
    CREATE FUNCTION notifications_check_status_transition() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        -- Only a pending notification changes status, and only once.
        IF OLD.status::text <> 'PENDING' THEN
            RAISE EXCEPTION 'notification % is already %', OLD.id, OLD.status
                USING ERRCODE = 'check_violation',
                      CONSTRAINT = 'ck_notifications_status_transition';
        END IF;
        RETURN NEW;
    END
    $$
    """,
    """
    CREATE TRIGGER notifications_status_transition
    BEFORE UPDATE OF status ON notifications
    FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status)
    EXECUTE FUNCTION notifications_check_status_transition()
    """,
)
DROP_TRIGGERS = (
    "DROP TRIGGER notifications_status_transition ON notifications",
    "DROP FUNCTION notifications_check_status_transition()",
    "DROP TRIGGER items_state_transition ON items",
    "DROP FUNCTION items_check_state_transition()",
    "DROP TRIGGER boxes_status_transition ON boxes",
    "DROP FUNCTION boxes_check_status_transition()",
)


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("moving_date", sa.Date(), nullable=False),
        sa.Column("origin_address", sa.String(length=500), nullable=True),
        sa.Column("destination_address", sa.String(length=500), nullable=True),
        sa.Column("qr_code_strategy", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "qr_code_strategy IN ('PROTECTED', 'OPEN')", name=op.f("ck_projects_qr_code_strategy")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_projects_owner_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
    )
    op.create_index(op.f("ix_projects_owner_id"), "projects", ["owner_id"], unique=False)
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint", sa.String(length=2048), nullable=False),
        sa.Column("p256dh", sa.String(length=256), nullable=False),
        sa.Column("auth", sa.String(length=256), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_push_subscriptions_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_push_subscriptions")),
        sa.UniqueConstraint("endpoint", name=op.f("uq_push_subscriptions_endpoint")),
    )
    op.create_index(
        "ix_push_subscriptions_user_id_active",
        "push_subscriptions",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text("is_active"),
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('PROJECT_CREATED', 'BOX_CREATED', 'BOX_STATUS_CHANGED', 'BOX_STATUS_ROLLED_BACK', 'BOX_DELETED', 'TASK_CREATED', 'TASK_COMPLETED', 'TASK_REOPENED', 'ITEM_MARKED_LOST', 'ITEM_RETURNED')",
            name=op.f("ck_audit_logs_action"),
        ),
        sa.CheckConstraint(
            "entity_type = split_part(action, '_', 1)",
            name=op.f("ck_audit_logs_entity_type_of_action"),
        ),
        sa.CheckConstraint(
            "entity_type IN ('PROJECT', 'BOX', 'TASK', 'ITEM')",
            name=op.f("ck_audit_logs_entity_type"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_audit_logs_project_id_projects")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(
        "ix_audit_logs_project_id_created_at",
        "audit_logs",
        ["project_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "project_collaborators",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_project_collaborators_project_id_projects"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_project_collaborators_user_id_users")
        ),
        sa.PrimaryKeyConstraint("project_id", "user_id", name=op.f("pk_project_collaborators")),
    )
    op.create_index(
        op.f("ix_project_collaborators_user_id"), "project_collaborators", ["user_id"], unique=False
    )
    op.create_table(
        "rooms",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.CheckConstraint("kind IN ('SOURCE', 'DESTINATION')", name=op.f("ck_rooms_kind")),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_rooms_project_id_projects")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rooms")),
        sa.UniqueConstraint("project_id", "id", name="uq_rooms_project_id_id"),
    )
    op.create_index(
        "uq_rooms_project_id_kind_name_active",
        "rooms",
        ["project_id", "kind", "name"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=5000), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_done", sa.Boolean(), nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "is_done = (done_at IS NOT NULL)", name=op.f("ck_tasks_done_at_iff_done")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_tasks_project_id_projects")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
        sa.UniqueConstraint("id", "project_id", name="uq_tasks_id_project_id"),
    )
    op.create_index(
        "ix_tasks_project_id_due_at_active",
        "tasks",
        ["project_id", "due_at"],
        unique=False,
        postgresql_where=sa.text("is_active"),
    )
    op.create_table(
        "boxes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("qr_token", sa.String(length=32), nullable=False),
        sa.Column("destination_room_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('PACKING', 'PACKED', 'LOADED', 'DELIVERED', 'UNPACKED')",
            name=op.f("ck_boxes_status"),
        ),
        sa.CheckConstraint("length(qr_token) = 22", name=op.f("ck_boxes_qr_token_length")),
        sa.CheckConstraint("number > 0", name=op.f("ck_boxes_number_positive")),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_boxes_created_by_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "destination_room_id"],
            ["rooms.project_id", "rooms.id"],
            name="fk_boxes_destination_room_id_rooms",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_boxes_project_id_projects")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_boxes")),
        sa.UniqueConstraint("project_id", "id", name="uq_boxes_project_id_id"),
        sa.UniqueConstraint("qr_token", name=op.f("uq_boxes_qr_token")),
    )
    op.create_index(
        "ix_boxes_project_id_destination_room_id_active",
        "boxes",
        ["project_id", "destination_room_id"],
        unique=False,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index(
        "ix_boxes_project_id_status_active",
        "boxes",
        ["project_id", "status"],
        unique=False,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index(
        "uq_boxes_project_id_number_active",
        "boxes",
        ["project_id", "number"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_table(
        "task_collaborators",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "user_id"],
            ["project_collaborators.project_id", "project_collaborators.user_id"],
            name="fk_task_collaborators_member_project_collaborators",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "project_id"],
            ["tasks.id", "tasks.project_id"],
            name="fk_task_collaborators_task_id_tasks",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("task_id", "user_id", name=op.f("pk_task_collaborators")),
    )
    op.create_index(
        "ix_task_collaborators_project_id_user_id",
        "task_collaborators",
        ["project_id", "user_id"],
        unique=False,
    )
    op.create_table(
        "task_reminders",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("offset_hours", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "offset_hours BETWEEN 1 AND 8760", name=op.f("ck_task_reminders_offset_hours_range")
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name=op.f("fk_task_reminders_task_id_tasks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("task_id", "offset_hours", name=op.f("pk_task_reminders")),
    )
    op.create_table(
        "items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("box_id", sa.Uuid(), nullable=True),
        sa.Column("source_room_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("priority", sa.SmallInteger(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(state = 'IN_BOX') = (box_id IS NOT NULL)", name=op.f("ck_items_box_id_iff_in_box")
        ),
        sa.CheckConstraint(
            "state IN ('TO_PACK', 'IN_BOX', 'TAKEN_OUT', 'LOST')", name=op.f("ck_items_state")
        ),
        sa.CheckConstraint("priority BETWEEN 0 AND 3", name=op.f("ck_items_priority_range")),
        sa.CheckConstraint("quantity BETWEEN 1 AND 10000", name=op.f("ck_items_quantity_range")),
        sa.ForeignKeyConstraint(
            ["project_id", "box_id"], ["boxes.project_id", "boxes.id"], name="fk_items_box_id_boxes"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "source_room_id"],
            ["rooms.project_id", "rooms.id"],
            name="fk_items_source_room_id_rooms",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_items_project_id_projects")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_items")),
    )
    op.create_index(
        "ix_items_box_id",
        "items",
        ["box_id"],
        unique=False,
        postgresql_where=sa.text("(box_id IS NOT NULL)"),
    )
    op.create_index(
        "ix_items_name_fts",
        "items",
        [sa.literal_column("to_tsvector('russian'::regconfig, name)")],
        unique=False,
        postgresql_using="gin",
        postgresql_where=sa.text("is_active"),
    )
    op.create_index(
        "ix_items_project_id_source_room_id_in_box",
        "items",
        ["project_id", "source_room_id"],
        unique=False,
        postgresql_where=sa.text("(is_active AND ((state)::text = 'IN_BOX'::text))"),
    )
    op.create_index(
        "ix_items_project_id_state_priority_active",
        "items",
        ["project_id", "state", "priority"],
        unique=False,
        postgresql_where=sa.text("is_active"),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("box_id", sa.Uuid(), nullable=True),
        sa.Column("reminder_offset_hours", sa.Integer(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(reminder_offset_hours IS NOT NULL) = (type = 'TASK_REMINDER')",
            name=op.f("ck_notifications_offset_iff_task_reminder"),
        ),
        sa.CheckConstraint(
            "(type = 'BOX_CONTENTS_REMINDER' AND box_id IS NOT NULL AND task_id IS NULL) OR (type IN ('TASK_ASSIGNED', 'TASK_REMINDER') AND task_id IS NOT NULL AND box_id IS NULL)",
            name=op.f("ck_notifications_target_matches_type"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'SENT', 'FAILED', 'CANCELLED', 'SKIPPED')",
            name=op.f("ck_notifications_status"),
        ),
        sa.CheckConstraint(
            "type IN ('TASK_ASSIGNED', 'TASK_REMINDER', 'BOX_CONTENTS_REMINDER')",
            name=op.f("ck_notifications_type"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_notifications_attempts_non_negative")),
        sa.ForeignKeyConstraint(
            ["box_id"], ["boxes.id"], name=op.f("fk_notifications_box_id_boxes")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_notifications_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name=op.f("fk_notifications_task_id_tasks")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_notifications_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    op.create_index(
        "ix_notifications_project_id_user_id_pending",
        "notifications",
        ["project_id", "user_id"],
        unique=False,
        postgresql_where=sa.text("((status)::text = 'PENDING'::text)"),
    )
    op.create_index(
        "ix_notifications_scheduled_at_pending",
        "notifications",
        ["scheduled_at"],
        unique=False,
        postgresql_where=sa.text("((status)::text = 'PENDING'::text)"),
    )
    op.create_index(
        "uq_notifications_box_contents_pending",
        "notifications",
        ["box_id", "user_id"],
        unique=True,
        postgresql_where=sa.text(
            "(((type)::text = 'BOX_CONTENTS_REMINDER'::text) AND ((status)::text = 'PENDING'::text))"
        ),
    )
    op.create_index(
        "uq_notifications_task_reminder_pending",
        "notifications",
        ["task_id", "user_id", "reminder_offset_hours"],
        unique=True,
        postgresql_where=sa.text(
            "(((type)::text = 'TASK_REMINDER'::text) AND ((status)::text = 'PENDING'::text))"
        ),
    )

    for statement in TRIGGERS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DROP_TRIGGERS:
        op.execute(statement)
    op.drop_index(
        "uq_notifications_task_reminder_pending",
        table_name="notifications",
        postgresql_where=sa.text(
            "(((type)::text = 'TASK_REMINDER'::text) AND ((status)::text = 'PENDING'::text))"
        ),
    )
    op.drop_index(
        "uq_notifications_box_contents_pending",
        table_name="notifications",
        postgresql_where=sa.text(
            "(((type)::text = 'BOX_CONTENTS_REMINDER'::text) AND ((status)::text = 'PENDING'::text))"
        ),
    )
    op.drop_index(
        "ix_notifications_scheduled_at_pending",
        table_name="notifications",
        postgresql_where=sa.text("((status)::text = 'PENDING'::text)"),
    )
    op.drop_index(
        "ix_notifications_project_id_user_id_pending",
        table_name="notifications",
        postgresql_where=sa.text("((status)::text = 'PENDING'::text)"),
    )
    op.drop_table("notifications")
    op.drop_index(
        "ix_items_project_id_state_priority_active",
        table_name="items",
        postgresql_where=sa.text("is_active"),
    )
    op.drop_index(
        "ix_items_project_id_source_room_id_in_box",
        table_name="items",
        postgresql_where=sa.text("(is_active AND ((state)::text = 'IN_BOX'::text))"),
    )
    op.drop_index(
        "ix_items_name_fts",
        table_name="items",
        postgresql_using="gin",
        postgresql_where=sa.text("is_active"),
    )
    op.drop_index(
        "ix_items_box_id", table_name="items", postgresql_where=sa.text("(box_id IS NOT NULL)")
    )
    op.drop_table("items")
    op.drop_table("task_reminders")
    op.drop_index("ix_task_collaborators_project_id_user_id", table_name="task_collaborators")
    op.drop_table("task_collaborators")
    op.drop_index(
        "uq_boxes_project_id_number_active",
        table_name="boxes",
        postgresql_where=sa.text("is_active"),
    )
    op.drop_index(
        "ix_boxes_project_id_status_active",
        table_name="boxes",
        postgresql_where=sa.text("is_active"),
    )
    op.drop_index(
        "ix_boxes_project_id_destination_room_id_active",
        table_name="boxes",
        postgresql_where=sa.text("is_active"),
    )
    op.drop_table("boxes")
    op.drop_index(
        "ix_tasks_project_id_due_at_active",
        table_name="tasks",
        postgresql_where=sa.text("is_active"),
    )
    op.drop_table("tasks")
    op.drop_index(
        "uq_rooms_project_id_kind_name_active",
        table_name="rooms",
        postgresql_where=sa.text("is_active"),
    )
    op.drop_table("rooms")
    op.drop_index(op.f("ix_project_collaborators_user_id"), table_name="project_collaborators")
    op.drop_table("project_collaborators")
    op.drop_index("ix_audit_logs_project_id_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index(
        "ix_push_subscriptions_user_id_active",
        table_name="push_subscriptions",
        postgresql_where=sa.text("is_active"),
    )
    op.drop_table("push_subscriptions")
    op.drop_index(op.f("ix_projects_owner_id"), table_name="projects")
    op.drop_table("projects")
