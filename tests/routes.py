"""Routes that exist only in tests: they exercise platform mechanisms no business
endpoint uses right now. Add them with `client_factory(configure_app=add_test_routes)`
or directly to an app built in a test."""

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import Depends, FastAPI, Response
from pydantic import BaseModel

from app.application.common.idempotency import IdempotentExecutor
from app.application.interfaces.idempotency import JsonValue
from app.presentation.http.app import API_PREFIX
from app.presentation.http.idempotency import IdempotencyKey, run_idempotent
from app.presentation.http.rate_limit import api_rate_limit

ECHO_PATH = f"{API_PREFIX}/test/echo"
ITEM_PATH = f"{API_PREFIX}/test/items"


class EchoRequest(BaseModel):
    text: str


def add_test_routes(app: FastAPI) -> None:
    """Routes for the platform mechanisms no business endpoint uses right now.

    `POST /api/v1/test/echo` runs through the idempotency executor and the api
    rate limit, like a real unsafe endpoint; it returns how often its action ran.
    `GET /api/v1/test/items/{item_id}` has a path parameter (metric labels).
    """
    runs = 0

    @app.post(ECHO_PATH, dependencies=[Depends(api_rate_limit)])
    @inject
    async def echo(
        body: EchoRequest,
        response: Response,
        executor: FromDishka[IdempotentExecutor],
        idempotency_key: IdempotencyKey = None,
    ) -> JsonValue:
        async def action() -> JsonValue:
            nonlocal runs
            runs += 1
            return {"text": body.text, "runs": runs}

        return await run_idempotent(
            executor,
            scope="echo",
            key=idempotency_key,
            payload={"text": body.text},
            response=response,
            action=action,
        )

    @app.get(f"{ITEM_PATH}/{{item_id}}")
    async def item(item_id: str) -> dict[str, str]:
        return {"id": item_id}
