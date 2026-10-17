from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide

from app.application.interfaces.push_keys import AbstractPushKeys
from app.application.interfaces.push_sender import AbstractPushSender
from app.config import PushSettings
from app.infrastructure.push.sender import WebPushSender
from app.infrastructure.push.vapid import VapidKeyStore


class PushProvider(Provider):
    """One key store and one sender (HTTP connection pool) per process."""

    scope = Scope.APP

    @provide
    def key_store(self, config: PushSettings) -> VapidKeyStore:
        private_key = config.vapid_private_key
        return VapidKeyStore(
            private_key_pem=None if private_key is None else private_key.get_secret_value(),
            key_file=config.vapid_key_file,
        )

    @provide
    def push_keys(self, store: VapidKeyStore) -> AbstractPushKeys:
        return store

    @provide
    async def push_sender(
        self, config: PushSettings, keys: VapidKeyStore
    ) -> AsyncIterator[AbstractPushSender]:
        sender = WebPushSender(
            keys=keys,
            subject=config.vapid_subject,
            ttl_seconds=config.ttl_seconds,
            timeout_seconds=config.timeout_seconds,
        )
        yield sender
        await sender.aclose()
