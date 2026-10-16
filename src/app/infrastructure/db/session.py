from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(url: str, *, pool_size: int, echo: bool = False) -> AsyncEngine:
    # pre_ping: a connection broken by a Postgres restart is replaced, not handed out.
    return create_async_engine(url, pool_size=pool_size, pool_pre_ping=True, echo=echo)


def create_session_maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: entities are mapped right after commit without reloading.
    return async_sessionmaker(engine, expire_on_commit=False)
