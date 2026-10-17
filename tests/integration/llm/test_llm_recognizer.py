"""LlmItemRecognizer against a Chat Completions API on localhost: the request it sends,
how it reads answers, and how every kind of failure is reported."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from prometheus_client import REGISTRY

from app.application.exceptions import ItemRecognitionUnavailableError
from app.application.interfaces.item_recognizer import Photo, RecognizedItem
from app.infrastructure.llm.recognizer import SYSTEM_PROMPT, LlmCallStatus, LlmItemRecognizer
from tests.conftest import LogEvents
from tests.integration.llm_service import (
    API_KEY,
    MODEL,
    FakeLlmService,
    completion,
    items_answer,
)

PHOTO = Photo(content=b"\x89PNG\r\n\x1a\n not really a png", media_type="image/png")
CLOSED_PORT_URL = "http://127.0.0.1:1/v1"


def make_recognizer(
    status: LlmCallStatus, *, base_url: str, timeout_seconds: float = 5.0
) -> LlmItemRecognizer:
    return LlmItemRecognizer(
        base_url=base_url,
        api_key=API_KEY,
        model=MODEL,
        timeout_seconds=timeout_seconds,
        max_output_tokens=512,
        status=status,
    )


@pytest.fixture
def status() -> LlmCallStatus:
    return LlmCallStatus()


@pytest.fixture
async def recognizer(
    llm_service: FakeLlmService, status: LlmCallStatus
) -> AsyncIterator[LlmItemRecognizer]:
    recognizer = make_recognizer(status, base_url=llm_service.base_url)
    yield recognizer
    await recognizer.aclose()


def calls(outcome: str) -> float:
    value = REGISTRY.get_sample_value(
        "llm_requests_total", {"operation": "scan_items", "outcome": outcome}
    )
    return value or 0.0


async def test_the_photo_goes_to_the_model_as_an_openai_compatible_request(
    llm_service: FakeLlmService, recognizer: LlmItemRecognizer
) -> None:
    await recognizer.recognize(PHOTO)

    [request] = llm_service.received
    assert request.path == "/v1/chat/completions"
    assert request.authorization == f"Bearer {API_KEY}"
    assert request.body["model"] == MODEL
    assert request.body["max_tokens"] == 512
    assert request.body["temperature"] == 0
    assert request.body["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert request.image == ("image/png", PHOTO.content)


async def test_the_items_of_the_answer_are_returned_and_the_call_logged(
    llm_service: FakeLlmService,
    recognizer: LlmItemRecognizer,
    status: LlmCallStatus,
    log_events: LogEvents,
) -> None:
    llm_service.answer(items_answer(("Кружка", 2), ("Чайник", 1)))
    ok_before = calls("ok")

    items = await recognizer.recognize(PHOTO)

    assert items == [RecognizedItem("Кружка", 2), RecognizedItem("Чайник", 1)]
    assert calls("ok") == ok_before + 1
    await status.check()  # healthy
    [event] = [e for e in log_events() if e["event"] == "llm_request_completed"]
    assert event["model"] == MODEL
    assert (event["prompt_tokens"], event["completion_tokens"], event["items"]) == (1200, 40, 2)
    assert API_KEY not in str(log_events())


async def test_content_parts_are_joined(
    llm_service: FakeLlmService, recognizer: LlmItemRecognizer
) -> None:
    answer = completion("")
    answer["choices"][0]["message"]["content"] = [
        {"type": "text", "text": '{"items": [{"name": '},
        {"type": "text", "text": '"Лампа", "quantity": 1}]}'},
    ]
    llm_service.answer(answer)

    assert await recognizer.recognize(PHOTO) == [RecognizedItem("Лампа", 1)]


@pytest.mark.parametrize(
    ("http_status", "body", "outcome"),
    [
        (429, "rate limit exceeded, retry after 12s", "rate_limited"),
        (401, "invalid token", "rejected"),
        (400, "model is blocked", "rejected"),
        (502, {"error": {"message": "provider is down"}}, "error"),
        (200, "<html>not json</html>", "bad_answer"),
        (200, {"choices": []}, "bad_answer"),
        (200, completion("Sorry, I can't help with that."), "bad_answer"),
    ],
)
async def test_failures_are_unavailable_counted_logged_and_degrade_readiness(
    llm_service: FakeLlmService,
    recognizer: LlmItemRecognizer,
    status: LlmCallStatus,
    log_events: LogEvents,
    http_status: int,
    body: Any,
    outcome: str,
) -> None:
    llm_service.answer(body, status=http_status)
    before = calls(outcome)

    with pytest.raises(ItemRecognitionUnavailableError):
        await recognizer.recognize(PHOTO)

    assert calls(outcome) == before + 1
    with pytest.raises(RuntimeError, match=outcome):
        await status.check()
    [event] = [e for e in log_events() if e["event"] == "llm_request_failed"]
    assert event["outcome"] == outcome
    assert event["http_status"] == http_status
    assert event["level"] == "warning"


async def test_a_slow_model_times_out(llm_service: FakeLlmService, status: LlmCallStatus) -> None:
    llm_service.delay_seconds = 2
    recognizer = make_recognizer(status, base_url=llm_service.base_url, timeout_seconds=0.2)
    before = calls("timeout")
    try:
        with pytest.raises(ItemRecognitionUnavailableError):
            await recognizer.recognize(PHOTO)
    finally:
        await recognizer.aclose()

    assert calls("timeout") == before + 1


async def test_an_unreachable_proxy_is_unavailable(status: LlmCallStatus) -> None:
    recognizer = make_recognizer(status, base_url=CLOSED_PORT_URL)
    try:
        with pytest.raises(ItemRecognitionUnavailableError):
            await recognizer.recognize(PHOTO)
    finally:
        await recognizer.aclose()

    with pytest.raises(RuntimeError, match="unreachable"):
        await status.check()


async def test_readiness_recovers_with_the_next_successful_call(
    llm_service: FakeLlmService, recognizer: LlmItemRecognizer, status: LlmCallStatus
) -> None:
    llm_service.answer("overloaded", status=503)
    with pytest.raises(ItemRecognitionUnavailableError):
        await recognizer.recognize(PHOTO)

    llm_service.answer(items_answer())
    await recognizer.recognize(PHOTO)

    await status.check()  # healthy again
