"""Item recognition by a vision model behind an OpenAI-compatible Chat Completions API:
the Spirit LLM proxy by default (docs/adr/0013-photo-scanning-with-llm.md).

The model is an auxiliary dependency: when it fails, only scanning answers 503, the
failure is logged and counted, and /health/ready reports `degraded`.
"""

import base64
import json
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import aiohttp
import structlog

from app.application.exceptions import (
    ItemRecognitionDisabledError,
    ItemRecognitionUnavailableError,
)
from app.application.interfaces.health import AbstractHealthProbe
from app.application.interfaces.item_recognizer import (
    AbstractItemRecognizer,
    Photo,
    RecognizedItem,
)
from app.infrastructure.metrics.prometheus import observe_llm_request

logger = structlog.get_logger(__name__)

OPERATION = "scan_items"
_ERROR_EXCERPT_LENGTH = 300

SYSTEM_PROMPT = """\
You help people pack for a move. List the movable items visible in the photo.

Answer with a single JSON object and nothing else, no prose and no code fences:
{"items": [{"name": "Настольная лампа", "quantity": 1}]}

- name: a short everyday name in Russian, singular, starting with a capital letter,
  at most 60 characters. Add a distinguishing detail only when it helps to find
  the item later ("Синяя кружка", "Зарядка для ноутбука").
- quantity: how many of this item are visible, an integer of at least 1.
- List each kind of item once. Skip people, pets, walls, floors, doors, windows,
  built-in fixtures and anything you cannot recognise with confidence.
- If no items are visible, answer {"items": []}.
"""
USER_PROMPT = "What items are in this photo?"


class LlmCallStatus(AbstractHealthProbe):
    """The outcome of the latest call, as an auxiliary readiness check.

    Never calls the paid API itself: it reports the latest real call, unhealthy from
    a failed one until the next successful one.
    """

    def __init__(self) -> None:
        self._last_failure: str | None = None

    def record_success(self) -> None:
        self._last_failure = None

    def record_failure(self, outcome: str) -> None:
        self._last_failure = outcome

    async def check(self) -> None:
        if self._last_failure is not None:
            raise RuntimeError(f"the last LLM call failed: {self._last_failure}")


class DisabledItemRecognizer(AbstractItemRecognizer):
    """Stands in when LLM__API_KEY is unset: scanning is off, the rest works."""

    async def recognize(self, photo: Photo) -> list[RecognizedItem]:  # noqa: ARG002
        raise ItemRecognitionDisabledError


_OK = 200


class _CallFailedError(Exception):
    def __init__(self, outcome: str, detail: str, *, http_status: int | None = None) -> None:
        super().__init__(detail)
        self.outcome = outcome
        self.detail = detail
        self.http_status = http_status


@dataclass(frozen=True, slots=True)
class _Answer:
    content: str
    usage: Mapping[str, Any]


class LlmItemRecognizer(AbstractItemRecognizer):
    """One HTTP session per process, created on the first call."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        status: LlmCallStatus,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._model = model
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._status = status
        self._session = session
        self._owns_session = session is None

    async def recognize(self, photo: Photo) -> list[RecognizedItem]:
        started = time.perf_counter()
        try:
            answer = await self._complete(photo)
            items = _parse_answer(answer.content)
        except _CallFailedError as failure:
            duration = time.perf_counter() - started
            observe_llm_request(operation=OPERATION, outcome=failure.outcome, duration=duration)
            self._status.record_failure(failure.outcome)
            logger.warning(
                "llm_request_failed",
                operation=OPERATION,
                model=self._model,
                outcome=failure.outcome,
                http_status=failure.http_status,
                error=failure.detail,
                duration_seconds=round(duration, 3),
            )
            raise ItemRecognitionUnavailableError from failure

        duration = time.perf_counter() - started
        observe_llm_request(operation=OPERATION, outcome="ok", duration=duration)
        self._status.record_success()
        logger.info(
            "llm_request_completed",
            operation=OPERATION,
            model=self._model,
            duration_seconds=round(duration, 3),
            prompt_tokens=answer.usage.get("prompt_tokens"),
            completion_tokens=answer.usage.get("completion_tokens"),
            items=len(items),
        )
        return items

    async def aclose(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    def _request_body(self, photo: Photo) -> dict[str, Any]:
        # No max_tokens: the model thinks by default and thinking counts against the
        # cap, so a cap cut answers off mid-JSON. The prompt keeps the answer short.
        image = base64.b64encode(photo.content).decode("ascii")
        return {
            "model": self._model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{photo.media_type};base64,{image}"},
                        },
                        {"type": "text", "text": USER_PROMPT},
                    ],
                },
            ],
        }

    async def _complete(self, photo: Photo) -> _Answer:
        try:
            async with self._http().post(
                self._url,
                json=self._request_body(photo),
                headers=self._headers,
                timeout=self._timeout,
            ) as response:
                status = response.status
                text = await response.text()
        # aiohttp's timeouts are TimeoutErrors, some of them ClientErrors too.
        except TimeoutError as error:
            raise _CallFailedError("timeout", repr(error)) from error
        except aiohttp.ClientError as error:
            raise _CallFailedError("unreachable", repr(error)) from error

        if status != _OK:
            raise _CallFailedError(
                _outcome_of(status), text[:_ERROR_EXCERPT_LENGTH], http_status=status
            )
        return _completion_of(text)


def _outcome_of(status: int) -> str:
    """Bounded labels for metrics: what an operator does next differs between them."""
    if status == 429:  # noqa: PLR2004
        return "rate_limited"  # quotas of the proxy account: wait or ask for more
    if 400 <= status < 500:  # noqa: PLR2004
        return "rejected"  # a bad key, a blocked model or provider: fix the config
    return "error"


def _completion_of(text: str) -> _Answer:
    try:
        payload = json.loads(text)
        choice = payload["choices"][0]
        content = choice["message"]["content"]
        finish_reason = choice.get("finish_reason")
        usage = payload.get("usage") or {}
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as error:
        raise _CallFailedError(
            "bad_answer", f"not a chat completion: {error!r}", http_status=_OK
        ) from error
    if not isinstance(usage, dict):
        usage = {}
    if finish_reason == "length":
        # The provider's own default cap (we send none) cut the answer off.
        raise _CallFailedError(
            "truncated",
            f"the answer hit max_tokens (completion_tokens={usage.get('completion_tokens')})",
            http_status=_OK,
        )
    if isinstance(content, list):  # some providers answer with content parts
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not isinstance(content, str):
        raise _CallFailedError("bad_answer", "the completion has no text", http_status=_OK)
    return _Answer(content=content, usage=usage)


def _parse_answer(content: str) -> list[RecognizedItem]:
    try:
        return parse_items(content)
    except ValueError as error:
        detail = f"{error}: {content[:_ERROR_EXCERPT_LENGTH]!r}"
        raise _CallFailedError("bad_answer", detail, http_status=_OK) from error


def parse_items(content: str) -> list[RecognizedItem]:
    """The items of the model's JSON answer.

    Tolerant to what models do despite the prompt: code fences and prose around the
    object, a bare list, quantities as strings or floats. Entries without a usable
    name are skipped; checking names against the domain is the use case's job.

    Raises:
        ValueError: no JSON object or list with items in the answer.
    """
    start = min((i for i in (content.find("{"), content.find("[")) if i >= 0), default=-1)
    end = max(content.rfind("}"), content.rfind("]"))
    if start < 0 or end < start:
        raise ValueError("no JSON in the answer")
    try:
        data = json.loads(content[start : end + 1])
    except ValueError as error:
        raise ValueError(f"invalid JSON in the answer: {error}") from error

    entries = data.get("items") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError("the answer has no list of items")
    return [item for entry in entries if (item := _item_of(entry)) is not None]


def _item_of(entry: object) -> RecognizedItem | None:
    if isinstance(entry, str):
        return RecognizedItem(entry)
    if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
        return None
    return RecognizedItem(entry["name"], _quantity_of(entry.get("quantity")))


def _quantity_of(value: object) -> int:
    if isinstance(value, bool):
        return 1
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return round(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 1
