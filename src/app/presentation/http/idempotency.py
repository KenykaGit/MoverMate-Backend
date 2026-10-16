from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Header, Response

from app.application.common.idempotency import IdempotentExecutor
from app.application.interfaces.idempotency import JsonValue

REPLAY_HEADER = "Idempotent-Replayed"

IdempotencyKey = Annotated[
    str | None,
    Header(
        min_length=8,
        max_length=128,
        description="Optional. Retries with the same key replay the first response.",
    ),
]


async def run_idempotent(
    executor: IdempotentExecutor,
    *,
    scope: str,
    key: str | None,
    payload: JsonValue,
    response: Response,
    action: Callable[[], Awaitable[JsonValue]],
) -> JsonValue:
    """Run `action` through the executor and mark replayed responses."""
    result = await executor.run(scope=scope, key=key, payload=payload, action=action)
    if result.replayed:
        response.headers[REPLAY_HEADER] = "true"
    return result.value
