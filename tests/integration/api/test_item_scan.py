"""POST /projects/{id}/items/scan through the real app, with the LLM on localhost."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient

from app.application.inventory.scan_items import MAX_PHOTO_BYTES
from app.config import RateLimitSettings
from app.domain.common.ids import new_id
from app.presentation.http.errors import ITEM_RECOGNITION_RETRY_AFTER_SECONDS
from tests.conftest import LogEvents
from tests.integration.conftest import ClientFactory
from tests.integration.helpers import API, Session, create_project, signed_up
from tests.integration.llm_service import FakeLlmService, items_answer

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF a photo of a kitchen shelf"


@pytest.fixture
async def scanning(
    client_factory: ClientFactory, llm_service: FakeLlmService
) -> AsyncIterator[AsyncClient]:
    """The app with scanning switched on."""
    async with client_factory(llm=llm_service.settings()) as client:
        yield client


async def scan(
    client: AsyncClient,
    project_id: str,
    session: Session | None,
    *,
    content: bytes = JPEG,
    content_type: str = "image/jpeg",
) -> Any:
    headers = {"Content-Type": content_type} | ({} if session is None else session.headers)
    return await client.post(
        f"{API}/projects/{project_id}/items/scan", content=content, headers=headers
    )


async def test_a_member_scans_a_photo_and_saves_the_items(
    scanning: AsyncClient, llm_service: FakeLlmService
) -> None:
    """The main path: photo -> suggestions -> the user saves them with createItems."""
    owner = await signed_up(scanning)
    project = await create_project(scanning, owner, rooms={"source": ["Кухня"], "destination": []})
    llm_service.answer(items_answer(("Кружка", 4), ("Чайник", 1)))

    response = await scan(scanning, project["id"], owner)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "items": [{"name": "Кружка", "quantity": 4}, {"name": "Чайник", "quantity": 1}]
    }
    [request] = llm_service.received
    assert request.image == ("image/jpeg", JPEG)

    rooms = await scanning.get(f"{API}/projects/{project['id']}/rooms", headers=owner.headers)
    saved = await scanning.post(
        f"{API}/projects/{project['id']}/items/batch",
        json={"source_room_id": rooms.json()[0]["id"], "entries": response.json()["items"]},
        headers=owner.headers,
    )
    assert saved.status_code == 201, saved.text
    assert [(i["name"], i["quantity"]) for i in saved.json()] == [("Кружка", 4), ("Чайник", 1)]


async def test_the_content_type_may_carry_parameters(
    scanning: AsyncClient, llm_service: FakeLlmService
) -> None:
    owner = await signed_up(scanning)
    project = await create_project(scanning, owner)

    response = await scan(scanning, project["id"], owner, content_type="Image/PNG; q=1")

    assert response.status_code == 200, response.text
    assert llm_service.received[0].image[0] == "image/png"


@pytest.mark.parametrize(
    ("content", "content_type", "status", "code"),
    [
        (b"", "image/jpeg", 422, "invalid_photo"),
        (b"x" * (MAX_PHOTO_BYTES + 1), "image/jpeg", 413, "photo_too_large"),
        (JPEG, "image/heic", 415, "unsupported_photo_type"),
        (b'{"image": "..."}', "application/json", 415, "unsupported_photo_type"),
    ],
)
async def test_a_bad_photo_is_rejected_before_the_model_is_asked(
    scanning: AsyncClient,
    llm_service: FakeLlmService,
    content: bytes,
    content_type: str,
    status: int,
    code: str,
) -> None:
    owner = await signed_up(scanning)
    project = await create_project(scanning, owner)

    response = await scan(
        scanning, project["id"], owner, content=content, content_type=content_type
    )

    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert llm_service.received == []


async def test_only_members_scan(scanning: AsyncClient, llm_service: FakeLlmService) -> None:
    owner = await signed_up(scanning)
    stranger = await signed_up(scanning, username="stranger")
    project = await create_project(scanning, owner)

    anonymous = await scan(scanning, project["id"], None)
    foreign = await scan(scanning, project["id"], stranger)
    missing = await scan(scanning, str(new_id()), owner)

    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "not_authenticated"
    assert [r.json()["error"]["code"] for r in (foreign, missing)] == ["project_not_found"] * 2
    assert llm_service.received == []


async def test_a_failing_model_is_503_with_retry_after_and_degrades_readiness(
    scanning: AsyncClient, llm_service: FakeLlmService, log_events: LogEvents
) -> None:
    """Only scanning is lost: the rest of the api keeps working, and it is visible."""
    owner = await signed_up(scanning)
    project = await create_project(scanning, owner)
    llm_service.answer("rate limit exceeded", status=429)

    response = await scan(scanning, project["id"], owner)
    ready = await scanning.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "item_recognition_unavailable"
    assert response.headers["Retry-After"] == str(ITEM_RECOGNITION_RETRY_AFTER_SECONDS)
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
    assert ready.status_code == 200
    assert ready.json()["status"] == "degraded"
    assert ready.json()["checks"]["llm"] == "degraded"
    [failure] = [e for e in log_events() if e["event"] == "llm_request_failed"]
    assert failure["outcome"] == "rate_limited"
    assert failure["request_id"] == response.headers["X-Request-ID"]

    llm_service.answer(items_answer(("Лампа", 1)))
    assert (await scan(scanning, project["id"], owner)).status_code == 200
    assert (await scanning.get("/health/ready")).json()["checks"]["llm"] == "ok"


async def test_without_an_api_key_scanning_is_off_and_readiness_is_ok(client: AsyncClient) -> None:
    owner = await signed_up(client)
    project = await create_project(client, owner)

    response = await scan(client, project["id"], owner)
    ready = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "item_recognition_disabled"
    assert "Retry-After" not in response.headers
    assert ready.json()["status"] == "ok"
    assert "llm" not in ready.json()["checks"]


async def test_scans_have_their_own_rate_limit(
    client_factory: ClientFactory, llm_service: FakeLlmService
) -> None:
    limits = RateLimitSettings(api_per_minute=100, auth_per_minute=0, scan_per_minute=1)
    async with client_factory(llm=llm_service.settings(), rate_limit=limits) as client:
        owner = await signed_up(client)
        project = await create_project(client, owner)

        first = await scan(client, project["id"], owner)
        second = await scan(client, project["id"], owner)
        other = await client.get(f"{API}/projects/{project['id']}", headers=owner.headers)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limited"
    assert second.headers["X-RateLimit-Limit"] == "1"
    assert "Retry-After" in second.headers
    assert other.status_code == 200
    assert len(llm_service.received) == 1
