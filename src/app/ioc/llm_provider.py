from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide

from app.application.interfaces.item_recognizer import AbstractItemRecognizer
from app.config import LlmSettings
from app.infrastructure.llm.recognizer import (
    DisabledItemRecognizer,
    LlmCallStatus,
    LlmItemRecognizer,
)


class LlmProvider(Provider):
    """One recognizer (HTTP connection pool) and its call status per process."""

    scope = Scope.APP

    call_status = provide(LlmCallStatus)

    @provide
    async def item_recognizer(
        self, config: LlmSettings, status: LlmCallStatus
    ) -> AsyncIterator[AbstractItemRecognizer]:
        if config.api_key is None:
            yield DisabledItemRecognizer()
            return
        recognizer = LlmItemRecognizer(
            base_url=config.base_url,
            api_key=config.api_key.get_secret_value(),
            model=config.scan_model,
            timeout_seconds=config.timeout_seconds,
            status=status,
        )
        yield recognizer
        await recognizer.aclose()
