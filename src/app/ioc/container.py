from dishka import AsyncContainer, Provider, make_async_container

from app.config import Settings
from app.ioc.config_provider import ConfigProvider
from app.ioc.db_provider import DatabaseProvider
from app.ioc.observability_provider import ObservabilityProvider
from app.ioc.push_provider import PushProvider
from app.ioc.redis_provider import RedisProvider
from app.ioc.security_provider import SecurityProvider
from app.ioc.use_cases_provider import UseCaseProvider


def create_container(settings: Settings, *extra_providers: Provider) -> AsyncContainer:
    """The dependency graph; validated on creation. Entry points add their own
    providers (e.g. FastapiProvider for the HTTP app)."""
    return make_async_container(
        ConfigProvider(),
        DatabaseProvider(),
        RedisProvider(),
        SecurityProvider(),
        PushProvider(),
        UseCaseProvider(),
        ObservabilityProvider(),
        *extra_providers,
        context={Settings: settings},
    )
