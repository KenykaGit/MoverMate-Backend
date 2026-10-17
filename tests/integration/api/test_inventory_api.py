"""Inventory endpoints through the real app: what happens before a use case runs.

Authentication and the shape of requests are checked here; the behaviour of the
use cases gets its own tests as they are implemented.
"""

from typing import Any

import pytest
from httpx import AsyncClient

from app.domain.common.ids import new_id
from tests.integration.helpers import API, signed_up

ID = str(new_id())

# (method, path, json body, query): valid in shape, so authentication decides.
OPERATIONS: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = [
    ("GET", f"/projects/{ID}/rooms", None, {"kind": "SOURCE"}),
    ("POST", f"/projects/{ID}/rooms", {"kind": "SOURCE", "name": "Kitchen"}, None),
    ("PATCH", f"/rooms/{ID}", {"name": "Hall"}, None),
    ("DELETE", f"/rooms/{ID}", None, None),
    ("GET", f"/projects/{ID}/boxes", None, {"status": ["PACKED", "LOADED"], "limit": 10}),
    ("POST", f"/projects/{ID}/boxes", {"destination_room_id": ID}, None),
    ("GET", f"/boxes/{ID}", None, None),
    ("PATCH", f"/boxes/{ID}", {"title": None, "is_fragile": True}, None),
    ("PUT", f"/boxes/{ID}/status", {"status": "PACKED"}, None),
    ("DELETE", f"/boxes/{ID}", None, None),
    ("PUT", f"/boxes/{ID}/contents-reminder", {"remind_at": "2026-11-01T09:00:00Z"}, None),
    ("DELETE", f"/boxes/{ID}/contents-reminder", None, None),
    ("GET", f"/projects/{ID}/items", None, {"state": "LOST", "priority": "FIRST_NECESSITY"}),
    ("POST", f"/projects/{ID}/items", {"name": "Kettle", "source_room_id": ID}, None),
    (
        "POST",
        f"/projects/{ID}/items/batch",
        {"source_room_id": ID, "entries": [{"name": "Mug", "quantity": 6}]},
        None,
    ),
    ("GET", f"/projects/{ID}/items/search", None, {"q": "заря"}),
    ("GET", f"/items/{ID}", None, None),
    ("PATCH", f"/items/{ID}", {"quantity": 2}, None),
    ("PUT", f"/items/{ID}/state", {"state": "IN_BOX", "box_id": ID}, None),
    ("DELETE", f"/items/{ID}", None, None),
    ("GET", f"/items/{ID}/suggestions", None, None),
]


@pytest.mark.parametrize(
    ("method", "path", "body", "query"),
    OPERATIONS,
    ids=[f"{method} {path.replace(ID, '{id}')}" for method, path, _, _ in OPERATIONS],
)
async def test_requires_a_user(
    client: AsyncClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    query: dict[str, Any] | None,
) -> None:
    response = await client.request(method, f"{API}{path}", json=body, params=query)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


async def test_box_by_qr_rejects_an_invalid_token(client: AsyncClient) -> None:
    """The token is optional there, but one that is sent must be valid."""
    response = await client.get(
        f"{API}/boxes/by-qr/AAAAAAAAAAAAAAAAAAAAAA",
        headers={"Authorization": "Bearer not-a-token"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


@pytest.mark.parametrize(
    ("method", "path", "body", "field"),
    [
        ("POST", f"/projects/{ID}/items/batch", {"source_room_id": ID, "entries": []}, "entries"),
        ("PATCH", f"/boxes/{ID}", {"is_fragile": None}, "is_fragile"),
        ("PATCH", f"/boxes/{ID}", {"destination_room_id": None}, "destination_room_id"),
        ("PATCH", f"/items/{ID}", {"name": None}, "name"),
        ("PATCH", f"/items/{ID}", {"priority": None}, "priority"),
        ("PUT", f"/items/{ID}/state", {"state": "MISPLACED"}, "state"),
        ("PUT", f"/boxes/{ID}/contents-reminder", {"remind_at": "2026-11-01T09:00"}, "remind_at"),
    ],
)
async def test_shape_is_validated_before_the_use_case(
    client: AsyncClient, method: str, path: str, body: dict[str, Any], field: str
) -> None:
    session = await signed_up(client)

    response = await client.request(method, f"{API}{path}", json=body, headers=session.headers)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert any(detail["field"].startswith(f"body.{field}") for detail in error["details"])
