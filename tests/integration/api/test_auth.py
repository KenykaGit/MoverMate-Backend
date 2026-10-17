import hashlib
from collections.abc import Callable

from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import AuthSettings, Settings
from tests.conftest import LogEvents
from tests.integration.conftest import TEST_SECRET_KEY, ClientFactory
from tests.integration.helpers import API, PASSWORD, login, register, signed_up


def assert_error(body: dict[str, object], code: str) -> None:
    error = body["error"]
    assert isinstance(error, dict)
    assert error["code"] == code
    assert set(error) == {"code", "message", "details", "request_id"}
    assert error["request_id"]


class TestRegister:
    async def test_returns_the_normalized_user_without_secrets(self, client: AsyncClient) -> None:
        response = await client.post(
            f"{API}/auth/register",
            json={"username": " John_Doe ", "password": PASSWORD},
        )

        assert response.status_code == 201
        body = response.json()
        assert set(body) == {"id", "username"}
        assert body["username"] == "john_doe"

    async def test_taken_username(self, client: AsyncClient) -> None:
        await register(client)

        response = await client.post(
            f"{API}/auth/register", json={"username": "JOHN_DOE", "password": PASSWORD}
        )

        assert response.status_code == 409
        assert_error(response.json(), "username_taken")

    async def test_invalid_bodies_are_422(self, client: AsyncClient) -> None:
        cases = [
            # An email is neither a field nor a username.
            (
                {"email": "john@example.com", "username": "john_doe", "password": PASSWORD},
                "validation_error",
            ),
            ({"username": "john@example.com", "password": PASSWORD}, "invalid_username"),
            ({"username": "john_doe", "password": "short"}, "invalid_password"),
        ]
        found = []
        for body, _ in cases:
            response = await client.post(f"{API}/auth/register", json=body)
            found.append((body, response.status_code, response.json()["error"]["code"]))

        assert found == [(body, 422, code) for body, code in cases]

    async def test_malformed_body_names_every_field(self, client: AsyncClient) -> None:
        response = await client.post(f"{API}/auth/register", json={"extra": 1})

        assert response.status_code == 422
        body = response.json()
        assert_error(body, "validation_error")
        fields = {detail["field"] for detail in body["error"]["details"]}
        assert {"body.username", "body.password", "body.extra"} <= fields


class TestLogin:
    async def test_by_username(self, client: AsyncClient) -> None:
        await register(client)

        for username in ("john_doe", "JOHN_DOE"):
            tokens = await login(client, username)
            assert tokens["token_type"] == "bearer"
            assert 0 < int(tokens["expires_in"]) <= 15 * 60

    async def test_wrong_password_and_unknown_username_look_the_same(
        self, client: AsyncClient
    ) -> None:
        await register(client)

        wrong = await client.post(
            f"{API}/auth/login", json={"username": "john_doe", "password": "wrong password"}
        )
        unknown = await client.post(
            f"{API}/auth/login", json={"username": "nobody", "password": "wrong password"}
        )

        for response in (wrong, unknown):
            assert response.status_code == 401
            assert response.headers["WWW-Authenticate"] == "Bearer"
            assert_error(response.json(), "invalid_credentials")
        assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]

    async def test_only_the_hash_of_the_refresh_token_is_stored(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        session_tokens = await signed_up(client)

        stored = (await session.execute(text("SELECT token_hash FROM refresh_tokens"))).scalars()

        expected = hashlib.sha256(session_tokens.refresh_token.encode()).hexdigest()
        assert list(stored) == [expected]

    async def test_outdated_password_hash_is_upgraded(
        self,
        client_factory: ClientFactory,
        make_settings: Callable[..., Settings],
        session: AsyncSession,
    ) -> None:
        """Raising the argon2 cost takes effect for each user at their next login."""
        stronger = AuthSettings(
            secret_key=SecretStr(TEST_SECRET_KEY),
            argon2_time_cost=2,
            argon2_memory_cost_kib=16,
            argon2_parallelism=1,
        )
        cheap = make_settings().auth
        async with client_factory() as client:
            await register(client)
        old_hash = await self._password_hash(session)

        async with client_factory(auth=stronger) as client:
            await login(client, "john_doe")
        new_hash = await self._password_hash(session)
        async with client_factory(auth=stronger) as client:
            await login(client, "john_doe")  # the new hash works

        assert f"m={cheap.argon2_memory_cost_kib},t={cheap.argon2_time_cost}," in old_hash
        assert "m=16,t=2,p=1$" in new_hash
        assert await self._password_hash(session) == new_hash, "upgraded once"

    @staticmethod
    async def _password_hash(session: AsyncSession) -> str:
        hashes = await session.execute(text("SELECT password_hash FROM users"))
        await session.rollback()  # the next read sees other transactions' commits
        [password_hash] = hashes.scalars()
        return str(password_hash)


class TestRefresh:
    async def test_rotation(self, client: AsyncClient) -> None:
        session = await signed_up(client)

        response = await client.post(
            f"{API}/auth/refresh", json={"refresh_token": session.refresh_token}
        )

        assert response.status_code == 200
        new = response.json()
        assert new["refresh_token"] != session.refresh_token
        me = await client.get(
            f"{API}/users/me", headers={"Authorization": f"Bearer {new['access_token']}"}
        )
        assert me.json()["id"] == session.user_id

    async def test_reuse_ends_that_session_only(
        self, client: AsyncClient, log_events: LogEvents
    ) -> None:
        first = await signed_up(client)
        second_device = await login(client, "john_doe")
        rotated = await client.post(
            f"{API}/auth/refresh", json={"refresh_token": first.refresh_token}
        )

        replay = await client.post(
            f"{API}/auth/refresh", json={"refresh_token": first.refresh_token}
        )
        after_replay = await client.post(
            f"{API}/auth/refresh", json={"refresh_token": rotated.json()["refresh_token"]}
        )
        other_session = await client.post(
            f"{API}/auth/refresh", json={"refresh_token": second_device["refresh_token"]}
        )

        assert replay.status_code == 401
        assert_error(replay.json(), "invalid_refresh_token")
        assert after_replay.status_code == 401, "the whole family of the leaked token is revoked"
        assert other_session.status_code == 200, "other devices keep their sessions"
        reused = [e for e in log_events() if e["event"] == "refresh_token_reused"]
        assert reused[0]["user_id"] == first.user_id

    async def test_logout(self, client: AsyncClient) -> None:
        session = await signed_up(client)

        response = await client.post(
            f"{API}/auth/logout", json={"refresh_token": session.refresh_token}
        )
        again = await client.post(
            f"{API}/auth/logout", json={"refresh_token": session.refresh_token}
        )
        refreshed = await client.post(
            f"{API}/auth/refresh", json={"refresh_token": session.refresh_token}
        )

        assert (response.status_code, again.status_code) == (204, 204)
        assert refreshed.status_code == 401

    async def test_logout_with_a_stale_token_ends_the_session(self, client: AsyncClient) -> None:
        session = await signed_up(client)
        rotated = await client.post(
            f"{API}/auth/refresh", json={"refresh_token": session.refresh_token}
        )

        await client.post(f"{API}/auth/logout", json={"refresh_token": session.refresh_token})
        current = await client.post(
            f"{API}/auth/refresh", json={"refresh_token": rotated.json()["refresh_token"]}
        )

        assert current.status_code == 401


class TestCurrentUser:
    async def test_requires_a_valid_bearer_token(self, client: AsyncClient) -> None:
        for headers in ({}, {"Authorization": "Bearer garbage"}, {"Authorization": "Basic x"}):
            response = await client.get(f"{API}/users/me", headers=headers)
            assert response.status_code == 401
            assert response.headers["WWW-Authenticate"] == "Bearer"
            assert_error(response.json(), "not_authenticated")
