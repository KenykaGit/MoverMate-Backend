from httpx import AsyncClient

from tests.integration.helpers import API, PASSWORD, register, signed_up

NEW_PASSWORD = "a brand new password"


async def test_me(client: AsyncClient) -> None:
    session = await signed_up(client)

    response = await client.get(f"{API}/users/me", headers=session.headers)

    assert response.json() == {
        "id": session.user_id,
        "email": "john@example.com",
        "username": "john_doe",
    }


async def test_change_password_ends_every_session(client: AsyncClient) -> None:
    session = await signed_up(client)

    response = await client.put(
        f"{API}/users/me/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers=session.headers,
    )

    assert response.status_code == 204
    refreshed = await client.post(
        f"{API}/auth/refresh", json={"refresh_token": session.refresh_token}
    )
    assert refreshed.status_code == 401
    old = await client.post(f"{API}/auth/login", json={"login": "john_doe", "password": PASSWORD})
    new = await client.post(
        f"{API}/auth/login", json={"login": "john_doe", "password": NEW_PASSWORD}
    )
    assert (old.status_code, new.status_code) == (401, 200)


async def test_change_password_requires_the_current_one(client: AsyncClient) -> None:
    session = await signed_up(client)

    response = await client.put(
        f"{API}/users/me/password",
        json={"current_password": "wrong password", "new_password": NEW_PASSWORD},
        headers=session.headers,
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


async def test_change_email_and_username(client: AsyncClient) -> None:
    session = await signed_up(client)
    await register(client, email="taken@example.com", username="taken")

    taken = await client.put(
        f"{API}/users/me/email",
        json={"new_email": "taken@example.com", "password": PASSWORD},
        headers=session.headers,
    )
    email = await client.put(
        f"{API}/users/me/email",
        json={"new_email": "New@Example.com", "password": PASSWORD},
        headers=session.headers,
    )
    username = await client.put(
        f"{API}/users/me/username", json={"new_username": "Johnny"}, headers=session.headers
    )

    assert taken.status_code == 409
    assert taken.json()["error"]["code"] == "email_taken"
    assert email.json()["email"] == "new@example.com"
    assert username.json()["username"] == "johnny"


async def test_soft_delete(client: AsyncClient) -> None:
    session = await signed_up(client)

    deleted = await client.delete(f"{API}/users/me", headers=session.headers)

    assert deleted.status_code == 204
    # The access token has not expired, but the user is re-read on every request.
    me = await client.get(f"{API}/users/me", headers=session.headers)
    assert me.status_code == 401
    refreshed = await client.post(
        f"{API}/auth/refresh", json={"refresh_token": session.refresh_token}
    )
    assert refreshed.status_code == 401
    login = await client.post(f"{API}/auth/login", json={"login": "john_doe", "password": PASSWORD})
    assert login.status_code == 401
    # The partial unique indexes free the email and username at once.
    newcomer = await register(client)
    assert newcomer["id"] != session.user_id
