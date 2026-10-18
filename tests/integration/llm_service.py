"""A fake OpenAI-compatible Chat Completions API on localhost (the LLM proxy's shape).

The recognizer under test runs unchanged: a real aiohttp request with the real body
and headers. The service records what it got and answers what the test told it.
"""

import asyncio
import base64
import json
from dataclasses import dataclass
from typing import Any

from aiohttp import web

from app.config import LlmSettings

API_KEY = "test-llm-key"
MODEL = "openrouter/claude-haiku-5-5"
_LOOPBACK = "127.0.0.1"


def completion(
    content: str,
    *,
    prompt_tokens: int = 1200,
    completion_tokens: int = 40,
    finish_reason: str = "stop",
) -> Any:
    """A Chat Completions response with one message."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def items_answer(*items: tuple[str, int]) -> Any:
    """A completion listing `items` as the prompt asks."""
    content = {"items": [{"name": name, "quantity": quantity} for name, quantity in items]}
    return completion(json.dumps(content, ensure_ascii=False))


@dataclass(frozen=True, slots=True)
class ReceivedCompletion:
    path: str
    authorization: str | None
    body: dict[str, Any]

    @property
    def image(self) -> tuple[str, bytes]:
        """The media type and bytes of the image in the user message."""
        [user] = [m for m in self.body["messages"] if m["role"] == "user"]
        [part] = [p for p in user["content"] if p["type"] == "image_url"]
        header, data = part["image_url"]["url"].split(",", 1)
        media_type = header.removeprefix("data:").removesuffix(";base64")
        return media_type, base64.b64decode(data)


class FakeLlmService:
    """Answers every completion with `status` and `body` (after `delay`, if set)."""

    def __init__(self) -> None:
        self._runner: web.AppRunner | None = None
        self._port = 0
        self.status = 200
        self.body: Any = items_answer()
        self.delay_seconds = 0.0
        self.received: list[ReceivedCompletion] = []

    @property
    def base_url(self) -> str:
        return f"http://{_LOOPBACK}:{self._port}/v1"

    def settings(self, **overrides: Any) -> LlmSettings:
        """Settings of an app that talks to this service."""
        return LlmSettings.model_validate(
            {"base_url": self.base_url, "api_key": API_KEY, "scan_model": MODEL} | overrides
        )

    def answer(self, body: Any, *, status: int = 200) -> None:
        self.status = status
        self.body = body

    async def start(self) -> None:
        app = web.Application(client_max_size=16 * 1024 * 1024)
        app.router.add_post("/{tail:.*}", self._handle)
        self._runner = web.AppRunner(app, shutdown_timeout=1)
        await self._runner.setup()
        site = web.TCPSite(self._runner, _LOOPBACK, 0)
        await site.start()
        server = site._server  # the only way to learn the bound port
        assert isinstance(server, asyncio.Server)
        self._port = server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    async def _handle(self, request: web.Request) -> web.Response:
        self.received.append(
            ReceivedCompletion(
                path=request.path,
                authorization=request.headers.get("Authorization"),
                body=await request.json(),
            )
        )
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if isinstance(self.body, str):
            return web.Response(status=self.status, text=self.body)
        return web.json_response(self.body, status=self.status)
