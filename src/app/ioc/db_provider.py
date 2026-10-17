from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.audit_log_repository import AbstractAuditLogRepository
from app.application.interfaces.notification_repository import AbstractNotificationRepository
from app.application.interfaces.project_reader import AbstractProjectReader
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.refresh_token_repository import AbstractRefreshTokenRepository
from app.application.interfaces.room_repository import AbstractRoomRepository
from app.application.interfaces.user_repository import AbstractUserRepository
from app.config import DatabaseSettings
from app.infrastructure.db.health import DatabaseHealthProbe
from app.infrastructure.db.repositories.audit_log import SqlAlchemyAuditLogRepository
from app.infrastructure.db.repositories.notification import SqlAlchemyNotificationRepository
from app.infrastructure.db.repositories.project import SqlAlchemyProjectRepository
from app.infrastructure.db.repositories.project_reader import SqlAlchemyProjectReader
from app.infrastructure.db.repositories.refresh_token import SqlAlchemyRefreshTokenRepository
from app.infrastructure.db.repositories.room import SqlAlchemyRoomRepository
from app.infrastructure.db.repositories.user import SqlAlchemyUserRepository
from app.infrastructure.db.session import create_engine, create_session_maker
from app.infrastructure.db.uow import SqlAlchemyUnitOfWork


class DatabaseProvider(Provider):
    @provide(scope=Scope.APP)
    async def engine(self, config: DatabaseSettings) -> AsyncIterator[AsyncEngine]:
        engine = create_engine(
            config.url.get_secret_value(), pool_size=config.pool_size, echo=config.echo
        )
        yield engine
        await engine.dispose()

    @provide(scope=Scope.APP)
    def session_maker(self, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
        return create_session_maker(engine)

    @provide(scope=Scope.REQUEST)
    async def session(
        self, session_maker: async_sessionmaker[AsyncSession]
    ) -> AsyncIterator[AsyncSession]:
        # Closing the session rolls back everything a use case did not commit.
        async with session_maker() as session:
            yield session

    health_probe = provide(DatabaseHealthProbe, scope=Scope.APP)

    # One session per request, shared by the unit of work and every repository.
    uow = provide(SqlAlchemyUnitOfWork, scope=Scope.REQUEST, provides=AbstractUnitOfWork)
    user_repository = provide(
        SqlAlchemyUserRepository, scope=Scope.REQUEST, provides=AbstractUserRepository
    )
    refresh_token_repository = provide(
        SqlAlchemyRefreshTokenRepository,
        scope=Scope.REQUEST,
        provides=AbstractRefreshTokenRepository,
    )
    project_repository = provide(
        SqlAlchemyProjectRepository, scope=Scope.REQUEST, provides=AbstractProjectRepository
    )
    project_reader = provide(
        SqlAlchemyProjectReader, scope=Scope.REQUEST, provides=AbstractProjectReader
    )
    room_repository = provide(
        SqlAlchemyRoomRepository, scope=Scope.REQUEST, provides=AbstractRoomRepository
    )
    notification_repository = provide(
        SqlAlchemyNotificationRepository,
        scope=Scope.REQUEST,
        provides=AbstractNotificationRepository,
    )
    audit_log_repository = provide(
        SqlAlchemyAuditLogRepository, scope=Scope.REQUEST, provides=AbstractAuditLogRepository
    )
