from dataclasses import dataclass

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
