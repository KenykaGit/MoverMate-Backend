import json

import structlog
from redis.asyncio import Redis

from app.application.interfaces.idempotency import (
    AbstractIdempotencyStore,
    BeginOutcome,
    BeginResult,
    IdempotencyRecord,
    IdempotencyState,
    JsonValue,
)
from app.infrastructure.redis.client import REDIS_FAILURES, report_redis_failure

logger = structlog.get_logger(__name__)

# SET NX may fail and the following GET find nothing if the record expired in
# between; then try to claim the key again, a bounded number of times.
_BEGIN_ATTEMPTS = 3


class RedisIdempotencyStore(AbstractIdempotencyStore):
    def __init__(self, redis: Redis, *, pending_ttl_seconds: int, completed_ttl_seconds: int):
        self._redis = redis
        self._pending_ttl = pending_ttl_seconds
        self._completed_ttl = completed_ttl_seconds

    async def begin(self, key: str, fingerprint: str) -> BeginResult:
        pending = _dump(IdempotencyRecord(state=IdempotencyState.PENDING, fingerprint=fingerprint))
        for _ in range(_BEGIN_ATTEMPTS):
            try:
                if await self._redis.set(key, pending, nx=True, ex=self._pending_ttl):
                    return BeginResult(BeginOutcome.STARTED)
                raw = await self._redis.get(key)
            except REDIS_FAILURES as error:
                report_redis_failure("idempotency_begin", error)
                return BeginResult(BeginOutcome.UNAVAILABLE)
            if raw is not None:
                break
        else:
            return BeginResult(BeginOutcome.UNAVAILABLE)

        try:
            record = _load(raw)
        except ValueError, KeyError, TypeError:
            logger.warning("idempotency_record_corrupted", key=key)
            return BeginResult(BeginOutcome.UNAVAILABLE)
        return BeginResult(BeginOutcome.EXISTS, record)

    async def complete(self, key: str, fingerprint: str, response: JsonValue) -> None:
        record = IdempotencyRecord(
            state=IdempotencyState.COMPLETED, fingerprint=fingerprint, response=response
        )
        try:
            await self._redis.set(key, _dump(record), ex=self._completed_ttl)
        except REDIS_FAILURES as error:
            report_redis_failure("idempotency_complete", error)

    async def release(self, key: str) -> None:
        try:
            await self._redis.delete(key)
        except REDIS_FAILURES as error:
            # The pending record expires on its own after its short TTL.
            report_redis_failure("idempotency_release", error)


def _dump(record: IdempotencyRecord) -> str:
    return json.dumps(
        {
            "state": record.state.value,
            "fingerprint": record.fingerprint,
            "response": record.response,
        }
    )


def _load(raw: str | bytes) -> IdempotencyRecord:
    data = json.loads(raw)
    return IdempotencyRecord(
        state=IdempotencyState(data["state"]),
        fingerprint=str(data["fingerprint"]),
        response=data["response"],
    )
