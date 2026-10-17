import secrets
from collections.abc import AsyncIterator
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, make_url, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.infrastructure.db.errors import violated_constraint
from app.infrastructure.db.models import Base
from tests.integration.conftest import alembic_config
from tests.integration.migrations.rows import insert_user, row_id


def test_single_head() -> None:
    """Parallel branches adding the same revision number are caught here."""
    script = ScriptDirectory.from_config(alembic_config("postgresql+asyncpg://unused"))

    assert len(script.get_heads()) == 1


def test_revisions_are_numbered_sequentially() -> None:
    script = ScriptDirectory.from_config(alembic_config("postgresql+asyncpg://unused"))
    revisions = list(reversed(list(script.walk_revisions())))

    assert [revision.revision for revision in revisions] == [
        f"{number:04d}" for number in range(1, len(revisions) + 1)
    ]


async def test_schema_matches_the_models(engine: AsyncEngine) -> None:
    """The schema after `upgrade head` is exactly what the models describe:
    catches a model change without a migration, and vice versa."""

    def diff(connection: Connection) -> list[Any]:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True}
        )
        result: list[Any] = compare_metadata(context, Base.metadata)
        return result

    async with engine.connect() as connection:
        assert await connection.run_sync(diff) == []


async def test_partial_index_predicates_match_the_models(engine: AsyncEngine) -> None:
    """Autogenerate ignores WHERE clauses of partial indexes: compare them here."""
    expected = {
        index.name: str(index.dialect_options["postgresql"]["where"])
        for table in Base.metadata.tables.values()
        for index in table.indexes
        if index.dialect_options["postgresql"]["where"] is not None
    }

    async with engine.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT index_class.relname, pg_get_expr(ix.indpred, ix.indrelid) "
                "FROM pg_index AS ix "
                "JOIN pg_class AS index_class ON index_class.oid = ix.indexrelid "
                "JOIN pg_namespace AS ns ON ns.oid = index_class.relnamespace "
                "WHERE ix.indpred IS NOT NULL AND ns.nspname = current_schema()"
            )
        )
        actual: dict[str, str] = {row[0]: row[1] for row in rows}

    assert (
        actual
        == expected
        == {
            "uq_users_username_active": "is_active",
            "uq_rooms_project_id_kind_name_active": "is_active",
            "uq_boxes_project_id_number_active": "is_active",
            "ix_boxes_project_id_status_active": "is_active",
            "ix_boxes_project_id_destination_room_id_active": "is_active",
            "ix_items_name_fts": "is_active",
            "ix_items_box_id": "(box_id IS NOT NULL)",
            "ix_items_project_id_state_priority_active": "is_active",
            "ix_items_project_id_source_room_id_in_box": (
                "(is_active AND ((state)::text = 'IN_BOX'::text))"
            ),
            "ix_tasks_project_id_due_at_active": "is_active",
            "ix_notifications_scheduled_at_pending": "((status)::text = 'PENDING'::text)",
            "ix_notifications_project_id_user_id_pending": "((status)::text = 'PENDING'::text)",
            "uq_notifications_task_reminder_pending": (
                "(((type)::text = 'TASK_REMINDER'::text) AND ((status)::text = 'PENDING'::text))"
            ),
            "uq_notifications_box_contents_pending": (
                "(((type)::text = 'BOX_CONTENTS_REMINDER'::text)"
                " AND ((status)::text = 'PENDING'::text))"
            ),
            "ix_push_subscriptions_user_id_active": "is_active",
        }
    )


async def test_soft_deleted_users_do_not_block_their_username(connection: AsyncConnection) -> None:
    await insert_user(connection, username="john", is_active=False)
    await insert_user(connection, username="john", is_active=False)
    await insert_user(connection, username="john")

    with pytest.raises(IntegrityError) as exc_info:
        async with connection.begin_nested():
            await insert_user(connection, username="john")

    assert violated_constraint(exc_info.value) == "uq_users_username_active"


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ({"username": "John"}, "ck_users_username_lowercase"),
    ],
)
async def test_check_constraints(
    connection: AsyncConnection, values: dict[str, str], constraint: str
) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        async with connection.begin_nested():
            await insert_user(connection, **values)

    assert violated_constraint(exc_info.value) == constraint


async def test_only_sha256_hashes_of_refresh_tokens_are_stored(
    connection: AsyncConnection,
) -> None:
    user_id = await insert_user(connection)

    with pytest.raises(IntegrityError) as exc_info:
        async with connection.begin_nested():
            await connection.execute(
                text(
                    "INSERT INTO refresh_tokens (id, user_id, family_id, token_hash, expires_at) "
                    "VALUES (:id, :user_id, :family_id, 'a-raw-token', now())"
                ),
                {"id": row_id(), "user_id": user_id, "family_id": row_id()},
            )

    assert violated_constraint(exc_info.value) == "ck_refresh_tokens_token_hash_sha256"


@pytest.fixture
async def scratch_database_url(engine: AsyncEngine) -> AsyncIterator[str]:
    """An empty database next to the test one: the roundtrip drops everything."""
    name = f"migrations_{secrets.token_hex(6)}"
    admin = engine.execution_options(isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    yield (
        make_url(str(engine.url.render_as_string(hide_password=False)))
        .set(database=name)
        .render_as_string(hide_password=False)
    )
    async with admin.connect() as connection:
        await connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))


async def _tables(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
        )
        names = set(rows.scalars())
    await engine.dispose()
    return names


def test_upgrade_downgrade_upgrade_roundtrip(scratch_database_url: str) -> None:
    """Every downgrade() works, and the schema can be rebuilt after it.

    Sync test: env.py runs its own event loop (asyncio.run).
    """
    import asyncio  # noqa: PLC0415

    config = alembic_config(scratch_database_url)

    command.upgrade(config, "head")
    command.downgrade(config, "base")
    left = asyncio.run(_tables(scratch_database_url))
    command.upgrade(config, "head")
    rebuilt = asyncio.run(_tables(scratch_database_url))

    assert left == {"alembic_version"}
    assert rebuilt == {"alembic_version", *Base.metadata.tables}
