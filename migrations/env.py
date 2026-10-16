import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import MigrationSettings
from app.infrastructure.db.models import Base

config = context.config
# Tests run migrations in-process and keep their own logging configuration.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    # An explicit URL (set by tests) wins over the environment.
    explicit = config.attributes.get("database_url")
    if isinstance(explicit, str):
        return explicit
    return MigrationSettings().database.url.get_secret_value()


def _configure(**kwargs: object) -> None:
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        **kwargs,  # type: ignore[arg-type]
    )


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: render SQL for review without a database."""
    _configure(url=_database_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
