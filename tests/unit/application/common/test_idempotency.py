import pytest

from app.application.common.idempotency import IdempotentExecutor, fingerprint
from app.application.exceptions import IdempotencyKeyReusedError, RequestInProgressError
from app.application.interfaces.idempotency import IdempotencyState, JsonValue
from tests.fakes import InMemoryIdempotencyStore

KEY = "client-key-1"
PAYLOAD: JsonValue = {"name": "Ship it", "tags": ["a", "b"]}


class Action:
    """Counts runs; returns a different value each time, like a create would."""

    def __init__(self, *, fails: bool = False) -> None:
        self.runs = 0
        self.fails = fails

    async def __call__(self) -> JsonValue:
        self.runs += 1
        if self.fails:
            raise RuntimeError("use case failed")
        return {"id": self.runs}


@pytest.fixture
def store() -> InMemoryIdempotencyStore:
    return InMemoryIdempotencyStore()


@pytest.fixture
def executor(store: InMemoryIdempotencyStore) -> IdempotentExecutor:
    return IdempotentExecutor(store)


async def run(
    executor: IdempotentExecutor,
    action: Action,
    *,
    key: str | None = KEY,
    payload: JsonValue = PAYLOAD,
    scope: str = "create",
) -> tuple[JsonValue, bool]:
    result = await executor.run(scope=scope, key=key, payload=payload, action=action)
    return result.value, result.replayed


async def test_without_key_always_runs(executor: IdempotentExecutor) -> None:
    action = Action()

    assert await run(executor, action, key=None) == ({"id": 1}, False)
    assert await run(executor, action, key=None) == ({"id": 2}, False)


async def test_repeat_with_same_key_replays_the_first_result(
    executor: IdempotentExecutor, store: InMemoryIdempotencyStore
) -> None:
    action = Action()

    assert await run(executor, action) == ({"id": 1}, False)
    assert await run(executor, action) == ({"id": 1}, True)
    assert action.runs == 1
    [record] = store.records.values()
    assert record.state is IdempotencyState.COMPLETED


async def test_fingerprint_ignores_key_order() -> None:
    assert fingerprint({"a": 1, "b": [1, 2]}) == fingerprint({"b": [1, 2], "a": 1})
    assert fingerprint({"a": 1}) != fingerprint({"a": 2})


async def test_same_key_with_another_payload_is_rejected(executor: IdempotentExecutor) -> None:
    action = Action()
    await run(executor, action)

    with pytest.raises(IdempotencyKeyReusedError):
        await run(executor, action, payload={"title": "Something else"})
    assert action.runs == 1


async def test_same_key_while_the_first_request_runs(
    executor: IdempotentExecutor, store: InMemoryIdempotencyStore
) -> None:
    """The second request arrives while the first one still holds PENDING."""

    async def concurrent_duplicate() -> JsonValue:
        with pytest.raises(RequestInProgressError):
            await run(executor, Action())
        return {"id": "first"}

    result = await executor.run(
        scope="create", key=KEY, payload=PAYLOAD, action=concurrent_duplicate
    )

    assert result.value == {"id": "first"}
    assert next(iter(store.records.values())).state is IdempotencyState.COMPLETED


async def test_failure_releases_the_key_so_the_client_can_retry(
    executor: IdempotentExecutor, store: InMemoryIdempotencyStore
) -> None:
    with pytest.raises(RuntimeError):
        await run(executor, Action(fails=True))
    assert store.records == {}

    assert await run(executor, Action()) == ({"id": 1}, False)


async def test_scopes_separate_equal_keys(executor: IdempotentExecutor) -> None:
    action = Action()

    await run(executor, action, scope="create_item:user-1")
    await run(executor, action, scope="create_item:user-2")

    assert action.runs == 2


async def test_store_outage_fails_open(
    executor: IdempotentExecutor, store: InMemoryIdempotencyStore
) -> None:
    store.available = False
    action = Action()

    assert await run(executor, action) == ({"id": 1}, False)
    assert await run(executor, action) == ({"id": 2}, False)  # no protection, but works
