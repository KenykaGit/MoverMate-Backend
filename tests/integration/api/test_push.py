"""Web Push endpoints: the VAPID key, subscribe (upsert by endpoint), unsubscribe."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.conftest import ClientFactory
from tests.integration.helpers import API, Session, signed_up
from tests.integration.push_service import Browser, b64url_decode

KEY_PATH = f"{API}/push/vapid-public-key"
SUBSCRIPTIONS = f"{API}/push/subscriptions"
ENDPOINT_MAX_LENGTH = 2048
KEY_MAX_LENGTH = 256


def browser(path: str = "browser-1") -> Browser:
    return Browser.create(f"https://fcm.googleapis.com/fcm/send/{path}")


async def subscribe(client: AsyncClient, session: Session, body: dict[str, Any]) -> int:
    response = await client.put(SUBSCRIPTIONS, json=body, headers=session.headers)
    return response.status_code


async def unsubscribe(client: AsyncClient, session: Session, endpoint: str) -> int:
    response = await client.delete(
        SUBSCRIPTIONS, params={"endpoint": endpoint}, headers=session.headers
    )
    return response.status_code


async def stored(engine: AsyncEngine) -> list[dict[str, Any]]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT user_id::text, endpoint, p256dh, auth, is_active"
                " FROM push_subscriptions ORDER BY created_at"
            )
        )
        return [dict(row) for row in rows.mappings()]


class TestVapidPublicKey:
    async def test_is_public_and_a_p256_point(self, client: AsyncClient) -> None:
        response = await client.get(KEY_PATH)

        assert response.status_code == 200
        key = response.json()["public_key"]
        raw = b64url_decode(key)
        assert (len(raw), raw[0]) == (65, 0x04), "what pushManager.subscribe() accepts"

    async def test_is_stable_across_calls_and_processes(
        self, client: AsyncClient, client_factory: ClientFactory
    ) -> None:
        first = (await client.get(KEY_PATH)).json()
        second = (await client.get(KEY_PATH)).json()
        async with client_factory() as restarted:
            after_restart = (await restarted.get(KEY_PATH)).json()

        assert first == second == after_restart, "a new key would break every subscription"


class TestSubscribe:
    async def test_stores_the_subscription(self, client: AsyncClient, engine: AsyncEngine) -> None:
        user = await signed_up(client)
        device = browser()

        response = await client.put(SUBSCRIPTIONS, json=device.to_json(), headers=user.headers)

        assert (response.status_code, response.content) == (204, b"")
        assert await stored(engine) == [
            {
                "user_id": user.user_id,
                "endpoint": device.endpoint,
                "p256dh": device.p256dh,
                "auth": device.auth,
                "is_active": True,
            }
        ]

    async def test_is_idempotent(self, client: AsyncClient, engine: AsyncEngine) -> None:
        user = await signed_up(client)
        body = browser().to_json()

        statuses = [await subscribe(client, user, body) for _ in range(3)]

        assert statuses == [204, 204, 204]
        assert len(await stored(engine)) == 1

    async def test_new_keys_of_the_same_browser_replace_the_old(
        self, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        user = await signed_up(client)
        old = browser()
        renewed = Browser.create(old.endpoint)
        await subscribe(client, user, old.to_json())

        assert await subscribe(client, user, renewed.to_json()) == 204

        [row] = await stored(engine)
        assert (row["p256dh"], row["auth"]) == (renewed.p256dh, renewed.auth)

    async def test_another_account_takes_the_browser_over(
        self, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        alice = await signed_up(client, username="alice")
        bob = await signed_up(client, username="bob")
        shared = browser()
        await subscribe(client, alice, shared.to_json())

        assert await subscribe(client, bob, shared.to_json()) == 204

        [row] = await stored(engine)
        assert (row["user_id"], row["is_active"]) == (bob.user_id, True)

    async def test_a_deactivated_subscription_revives(
        self, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        user = await signed_up(client)
        device = browser()
        await subscribe(client, user, device.to_json())
        await unsubscribe(client, user, device.endpoint)
        renewed = Browser.create(device.endpoint)

        assert await subscribe(client, user, renewed.to_json()) == 204

        [row] = await stored(engine)
        assert (row["is_active"], row["p256dh"]) == (True, renewed.p256dh)

    async def test_several_browsers_of_one_user(
        self, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        user = await signed_up(client)

        for path in ("phone", "laptop"):
            assert await subscribe(client, user, browser(path).to_json()) == 204

        assert [row["user_id"] for row in await stored(engine)] == [user.user_id] * 2

    @pytest.mark.parametrize("expiration", [None, 1_790_000_000_000, 1.5])
    async def test_expiration_time_is_accepted(
        self, client: AsyncClient, expiration: float | None
    ) -> None:
        user = await signed_up(client)
        body = browser().to_json() | {"expirationTime": expiration}

        assert await subscribe(client, user, body) == 204

    async def test_expiration_time_may_be_absent(self, client: AsyncClient) -> None:
        user = await signed_up(client)
        body = browser().to_json()
        del body["expirationTime"]

        assert await subscribe(client, user, body) == 204

    async def test_boundary_lengths_are_accepted(
        self, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        user = await signed_up(client)
        prefix = "https://push.example.com/"
        body = {
            "endpoint": prefix + "e" * (ENDPOINT_MAX_LENGTH - len(prefix)),
            "keys": {"p256dh": "A" * KEY_MAX_LENGTH, "auth": "B" * KEY_MAX_LENGTH},
        }

        assert await subscribe(client, user, body) == 204
        [row] = await stored(engine)
        assert len(row["endpoint"]) == ENDPOINT_MAX_LENGTH

    async def test_requires_a_token(self, client: AsyncClient, engine: AsyncEngine) -> None:
        response = await client.put(SUBSCRIPTIONS, json=browser().to_json())

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "not_authenticated"
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert await stored(engine) == []

    @pytest.mark.parametrize(
        ("endpoint", "keys"),
        [
            ("http://push.example.com/insecure", None),
            ("push.example.com/no-scheme", None),
            ("https://push.example.com/" + "e" * ENDPOINT_MAX_LENGTH, None),
            (None, {"p256dh": "not+base64/url", "auth": "AAAA"}),
            (None, {"p256dh": "AAAA", "auth": "with spaces"}),
            (None, {"p256dh": "", "auth": "AAAA"}),
            (None, {"p256dh": "A" * (KEY_MAX_LENGTH + 1), "auth": "AAAA"}),
        ],
        ids=[
            "http",
            "no-scheme",
            "endpoint-too-long",
            "p256dh-base64",
            "auth-spaces",
            "p256dh-empty",
            "p256dh-too-long",
        ],
    )
    async def test_rejects_an_unusable_subscription(
        self,
        client: AsyncClient,
        engine: AsyncEngine,
        endpoint: str | None,
        keys: dict[str, str] | None,
    ) -> None:
        user = await signed_up(client)
        body = browser().to_json()
        body["endpoint"] = endpoint or body["endpoint"]
        body["keys"] = keys or body["keys"]

        response = await client.put(SUBSCRIPTIONS, json=body, headers=user.headers)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_push_subscription"
        assert await stored(engine) == []

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"endpoint": "https://push.example.com/x"},
            {"endpoint": "https://push.example.com/x", "keys": {"p256dh": "AAAA"}},
            {"endpoint": 42, "keys": {"p256dh": "AAAA", "auth": "AAAA"}},
            {
                "endpoint": "https://push.example.com/x",
                "keys": {"p256dh": "AAAA", "auth": "AAAA"},
                "unknown": True,
            },
            {
                "endpoint": "https://push.example.com/x",
                "keys": {"p256dh": "AAAA", "auth": "AAAA", "extra": "x"},
            },
            {
                "endpoint": "https://push.example.com/x",
                "keys": {"p256dh": "AAAA", "auth": "AAAA"},
                "expirationTime": "tomorrow",
            },
        ],
        ids=[
            "empty",
            "no-keys",
            "no-auth",
            "endpoint-not-a-string",
            "unknown-field",
            "unknown-key-field",
            "expiration-not-a-number",
        ],
    )
    async def test_rejects_a_malformed_body(
        self, client: AsyncClient, engine: AsyncEngine, body: dict[str, Any]
    ) -> None:
        user = await signed_up(client)

        response = await client.put(SUBSCRIPTIONS, json=body, headers=user.headers)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
        assert response.json()["error"]["details"], "names the offending field"
        assert await stored(engine) == []


class TestUnsubscribe:
    async def test_deactivates_the_callers_subscription(
        self, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        user = await signed_up(client)
        device = browser()
        await subscribe(client, user, device.to_json())

        response = await client.delete(
            SUBSCRIPTIONS, params={"endpoint": device.endpoint}, headers=user.headers
        )

        assert (response.status_code, response.content) == (204, b"")
        [row] = await stored(engine)
        assert row["is_active"] is False

    async def test_is_idempotent(self, client: AsyncClient, engine: AsyncEngine) -> None:
        user = await signed_up(client)
        device = browser()
        await subscribe(client, user, device.to_json())

        statuses = [await unsubscribe(client, user, device.endpoint) for _ in range(2)]

        assert statuses == [204, 204]
        assert [row["is_active"] for row in await stored(engine)] == [False]

    async def test_leaves_other_browsers_of_the_user_alone(
        self, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        user = await signed_up(client)
        phone, laptop = browser("phone"), browser("laptop")
        await subscribe(client, user, phone.to_json())
        await subscribe(client, user, laptop.to_json())

        await unsubscribe(client, user, phone.endpoint)

        active = {row["endpoint"]: row["is_active"] for row in await stored(engine)}
        assert active == {phone.endpoint: False, laptop.endpoint: True}

    async def test_someone_elses_browser_stays_subscribed(
        self, client: AsyncClient, engine: AsyncEngine
    ) -> None:
        alice = await signed_up(client, username="alice")
        mallory = await signed_up(client, username="mallory")
        device = browser()
        await subscribe(client, alice, device.to_json())

        status = await unsubscribe(client, mallory, device.endpoint)

        assert status == 204, "the same answer as for an own one: reveals nothing"
        [row] = await stored(engine)
        assert (row["user_id"], row["is_active"]) == (alice.user_id, True)

    @pytest.mark.parametrize(
        "endpoint",
        ["https://push.example.com/never-subscribed", "http://not-even-https", ""],
        ids=["unknown", "not-https", "empty"],
    )
    async def test_unknown_endpoints_are_fine(self, client: AsyncClient, endpoint: str) -> None:
        user = await signed_up(client)

        assert await unsubscribe(client, user, endpoint) == 204

    async def test_endpoint_is_required(self, client: AsyncClient) -> None:
        user = await signed_up(client)

        response = await client.delete(SUBSCRIPTIONS, headers=user.headers)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    async def test_endpoint_length_is_bounded(self, client: AsyncClient) -> None:
        user = await signed_up(client)
        longest = "https://push.example.com/" + "e" * 4071

        accepted = await unsubscribe(client, user, longest)
        rejected = await client.delete(
            SUBSCRIPTIONS, params={"endpoint": longest + "e"}, headers=user.headers
        )

        assert (len(longest), accepted) == (4096, 204)
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "validation_error"

    async def test_requires_a_token(self, client: AsyncClient, engine: AsyncEngine) -> None:
        owner = await signed_up(client)
        device = browser()
        await subscribe(client, owner, device.to_json())

        response = await client.delete(SUBSCRIPTIONS, params={"endpoint": device.endpoint})

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "not_authenticated"
        assert [row["is_active"] for row in await stored(engine)] == [True]
