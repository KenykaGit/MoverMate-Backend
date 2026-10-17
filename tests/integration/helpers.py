from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from httpx import AsyncClient

PASSWORD = "correct horse battery"
API = "/api/v1"


@dataclass(frozen=True, slots=True)
class Session:
    user_id: str
    access_token: str
    refresh_token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


async def register(
    client: AsyncClient,
    *,
    username: str = "john_doe",
    password: str = PASSWORD,
) -> dict[str, str]:
    response = await client.post(
        f"{API}/auth/register", json={"username": username, "password": password}
    )
    assert response.status_code == 201, response.text
    body: dict[str, str] = response.json()
    return body


async def login(client: AsyncClient, username: str, password: str = PASSWORD) -> dict[str, str]:
    response = await client.post(
        f"{API}/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    body: dict[str, str] = response.json()
    return body


async def signed_up(client: AsyncClient, *, username: str = "john_doe") -> Session:
    """Register a user and log in."""
    user = await register(client, username=username)
    tokens = await login(client, username)
    return Session(user["id"], tokens["access_token"], tokens["refresh_token"])


def in_days(days: int) -> date:
    """A date relative to today (UTC, like the app's clock): moving dates stay in the
    future whenever tests run."""
    return datetime.now(UTC).date() + timedelta(days=days)


async def create_project(client: AsyncClient, owner: Session, **overrides: Any) -> dict[str, Any]:
    """Create a project of `owner` two months ahead; keyword arguments replace body fields."""
    body = {
        "name": "Moving to Kazan",
        "moving_date": in_days(60).isoformat(),
        "time_zone": "Europe/Moscow",
    } | overrides
    response = await client.post(f"{API}/projects", json=body, headers=owner.headers)
    assert response.status_code == 201, response.text
    project: dict[str, Any] = response.json()
    return project


async def add_collaborator(
    client: AsyncClient, project_id: str, by: Session, username: str
) -> dict[str, Any]:
    response = await client.post(
        f"{API}/projects/{project_id}/collaborators",
        json={"username": username},
        headers=by.headers,
    )
    assert response.status_code == 201, response.text
    collaborator: dict[str, Any] = response.json()
    return collaborator
