"""Idempotency-Key lifecycle: run an operation at most once per client key.

absent -> PENDING (short TTL) -> COMPLETED (long TTL)
                  \\-> deleted on failure, so the client may retry

A crashed worker leaves PENDING behind; it expires after its short TTL instead
of blocking the key for a day.
"""

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.application.exceptions import IdempotencyKeyReusedError, RequestInProgressError
from app.application.interfaces.idempotency import (
    AbstractIdempotencyStore,
    BeginOutcome,
    IdempotencyState,
    JsonValue,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IdempotentResult:
    value: JsonValue
    replayed: bool
    """True if `value` is the stored result of an earlier request."""


def fingerprint(payload: JsonValue) -> str:
    """Stable hash of a request payload: key order does not matter."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class IdempotentExecutor:
    def __init__(self, store: AbstractIdempotencyStore) -> None:
        self._store = store

    async def run(
        self,
        *,
        scope: str,
        key: str | None,
        payload: JsonValue,
        action: Callable[[], Awaitable[JsonValue]],
    ) -> IdempotentResult:
        """Run `action` once per (scope, key); repeated calls replay its result.

        `scope` separates keys of different operations and callers, e.g. the
        endpoint and the user id. Without a key the action simply runs.

        Raises:
            IdempotencyKeyReusedError: the key came with a different payload.
            RequestInProgressError: the first request with the key is still running.
        """
        if key is None:
            return IdempotentResult(await action(), replayed=False)

        storage_key = f"idem:{scope}:{key}"
        expected = fingerprint(payload)
        begin = await self._store.begin(storage_key, expected)

        if begin.outcome is BeginOutcome.UNAVAILABLE:
            # Fail-open: a retry may now run twice. Visible in redis_errors_total.
            return IdempotentResult(await action(), replayed=False)

        if begin.outcome is BeginOutcome.EXISTS:
            record = begin.record
            if record is None:  # pragma: no cover  # contract of AbstractIdempotencyStore
                raise RuntimeError("begin() returned EXISTS without a record")
            if record.fingerprint != expected:
                raise IdempotencyKeyReusedError
            if record.state is IdempotencyState.PENDING:
                raise RequestInProgressError
            logger.info("idempotent_replay", extra={"scope": scope})
            return IdempotentResult(record.response, replayed=True)

        try:
            value = await action()
        except BaseException:
            await self._store.release(storage_key)
            raise
        await self._store.complete(storage_key, expected, value)
        return IdempotentResult(value, replayed=False)
