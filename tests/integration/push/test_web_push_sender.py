"""WebPushSender against a real HTTPS push service, and the VAPID key store on disk.

Messages are decrypted on the receiving side with the browser's keys, so the
encryption is checked end to end, not mocked.
"""

import os
import stat
import time
from pathlib import Path

import aiohttp
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from py_vapid import Vapid

from app.application.interfaces.push_sender import PushMessage, PushOutcome, PushResult
from app.domain.common.ids import UserId, new_id
from app.domain.notifications.push_subscription import PushEndpoint, PushKeys, PushSubscription
from app.infrastructure.push.sender import WebPushSender
from app.infrastructure.push.vapid import (
    InvalidVapidKeyError,
    VapidKeyStore,
    application_server_key,
    load_or_create_private_key,
)
from tests.integration.push_service import (
    FAKE_HOST,
    Browser,
    FakePushService,
    b64url,
    b64url_decode,
    vapid_token,
    web_push_sender,
)

USER = UserId(new_id())
MESSAGE = PushMessage(
    title="Завтра переезд",
    body="Проект «Казань»: проверьте план и коробки",
    url="/dashboard",
    data={"type": "MOVING_DATE_REMINDER", "project_id": "p-1", "notification_id": "n-1"},
)
MAX_VAPID_LIFETIME_SECONDS = 24 * 60 * 60  # RFC 8292, section 2


@pytest.fixture
def keys(tmp_path: Path) -> VapidKeyStore:
    return VapidKeyStore(private_key_pem=None, key_file=tmp_path / "vapid" / "private_key.pem")


@pytest.fixture
def sender(keys: VapidKeyStore, push_session: aiohttp.ClientSession) -> WebPushSender:
    return web_push_sender(keys, push_session, ttl_seconds=60)


def pem(key: ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


class TestDelivery:
    async def test_the_browser_decrypts_the_message(
        self, sender: WebPushSender, push_service: FakePushService
    ) -> None:
        browser = push_service.browser()

        result = await sender.send(browser.subscription(USER), MESSAGE)

        assert result == PushResult(PushOutcome.DELIVERED)
        assert push_service.payloads(browser) == [
            {
                "title": "Завтра переезд",
                "body": "Проект «Казань»: проверьте план и коробки",
                "url": "/dashboard",
                "type": "MOVING_DATE_REMINDER",
                "project_id": "p-1",
                "notification_id": "n-1",
            }
        ]

    async def test_web_push_headers(
        self, sender: WebPushSender, push_service: FakePushService
    ) -> None:
        browser = push_service.browser()

        await sender.send(browser.subscription(USER), MESSAGE)

        [push] = push_service.received_by(browser)
        assert push.headers["Content-Encoding"] == "aes128gcm"
        assert push.headers["TTL"] == "60"
        assert push.headers["Urgency"] == "normal"

    async def test_vapid_token_is_signed_for_the_push_service_origin(
        self, sender: WebPushSender, keys: VapidKeyStore, push_service: FakePushService
    ) -> None:
        browser = push_service.browser()

        await sender.send(browser.subscription(USER), MESSAGE)

        [push] = push_service.received_by(browser)
        authorization = push.headers["Authorization"]
        header, claims, public_key = vapid_token(authorization)
        assert Vapid.verify(authorization), "the signature must verify with the sent key"
        assert public_key == keys.public_key(), "the key browsers subscribed with"
        assert header["alg"] == "ES256"
        assert claims["aud"] == push_service.origin  # no path: the endpoint is a secret
        assert claims["sub"] == "mailto:ops@example.com"
        assert time.time() < claims["exp"] <= time.time() + MAX_VAPID_LIFETIME_SECONDS

    @pytest.mark.parametrize("status", [200, 201, 202])
    async def test_accepted_statuses(
        self, sender: WebPushSender, push_service: FakePushService, status: int
    ) -> None:
        browser = push_service.browser()
        push_service.answer(browser, status)

        result = await sender.send(browser.subscription(USER), MESSAGE)

        assert result.outcome is PushOutcome.DELIVERED

    async def test_one_sender_serves_many_browsers(
        self, sender: WebPushSender, push_service: FakePushService
    ) -> None:
        browsers = [push_service.browser() for _ in range(3)]

        results = [await sender.send(b.subscription(USER), MESSAGE) for b in browsers]

        assert {r.outcome for r in results} == {PushOutcome.DELIVERED}
        assert all(push_service.payloads(b)[0]["title"] == MESSAGE.title for b in browsers)


class TestFailures:
    @pytest.mark.parametrize("status", [404, 410])
    async def test_unknown_subscription_is_gone(
        self, sender: WebPushSender, push_service: FakePushService, status: int
    ) -> None:
        browser = push_service.browser()
        push_service.answer(browser, status)

        result = await sender.send(browser.subscription(USER), MESSAGE)

        assert result == PushResult(
            PushOutcome.GONE, f"{FAKE_HOST}:{push_service.port} answered {status}"
        )

    @pytest.mark.parametrize("status", [400, 413, 429, 500, 503])
    async def test_other_errors_fail_without_revealing_the_endpoint(
        self, sender: WebPushSender, push_service: FakePushService, status: int
    ) -> None:
        browser = push_service.browser()
        push_service.answer(browser, status)

        result = await sender.send(browser.subscription(USER), MESSAGE)

        assert result == PushResult(
            PushOutcome.FAILED, f"{FAKE_HOST}:{push_service.port} answered {status}"
        )
        assert result.detail is not None
        assert browser.path not in result.detail

    async def test_connection_refused_fails(self, sender: WebPushSender) -> None:
        browser = Browser.create(f"https://{FAKE_HOST}:1/push/secret-token")  # closed port

        result = await sender.send(browser.subscription(USER), MESSAGE)

        assert result.outcome is PushOutcome.FAILED
        assert result.detail is not None
        assert result.detail.startswith(f"{FAKE_HOST}:1: ")
        assert "secret-token" not in result.detail

    async def test_timeout_fails(
        self,
        keys: VapidKeyStore,
        push_session: aiohttp.ClientSession,
        push_service: FakePushService,
    ) -> None:
        sender = web_push_sender(keys, push_session, timeout_seconds=0.2)
        browser = push_service.browser()
        push_service.hang(browser)

        result = await sender.send(browser.subscription(USER), MESSAGE)

        assert result.outcome is PushOutcome.FAILED
        assert result.detail is not None
        assert result.detail.startswith(f"{FAKE_HOST}:{push_service.port}: ")
        assert "Timeout" in result.detail

    @pytest.mark.parametrize(
        "p256dh",
        [
            "AAAA",  # base64url, but too short for a key
            b64url(b"\x04" + bytes(64)),  # the right length, not a point of the curve
        ],
        ids=["short", "off-curve"],
    )
    async def test_unusable_browser_keys_are_gone(
        self, sender: WebPushSender, push_service: FakePushService, p256dh: str
    ) -> None:
        browser = push_service.browser()
        subscription = PushSubscription.subscribe(
            user_id=USER,
            endpoint=PushEndpoint(browser.endpoint),
            keys=PushKeys(p256dh=p256dh, auth=browser.auth),
        )

        result = await sender.send(subscription, MESSAGE)

        assert result.outcome is PushOutcome.GONE
        assert result.detail is not None
        assert result.detail.startswith("invalid subscription keys")
        assert push_service.received == [], "nothing undecryptable is sent"


class TestVapidKeyStore:
    def test_creates_a_private_key_file_readable_by_the_owner_only(self, tmp_path: Path) -> None:
        key_file = tmp_path / "missing" / "dir" / "private_key.pem"
        store = VapidKeyStore(private_key_pem=None, key_file=key_file)

        public_key = store.public_key()

        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
        stored = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
        assert isinstance(stored, ec.EllipticCurvePrivateKey)
        raw = b64url_decode(public_key)
        assert (len(raw), raw[0]) == (65, 0x04), "an uncompressed P-256 point"
        assert "=" not in public_key, "browsers expect unpadded base64url"

    def test_another_process_reuses_the_key_file(self, tmp_path: Path) -> None:
        key_file = tmp_path / "private_key.pem"
        api = VapidKeyStore(private_key_pem=None, key_file=key_file)
        first = api.public_key()
        written = key_file.read_bytes()

        worker = VapidKeyStore(private_key_pem=None, key_file=key_file)

        assert worker.public_key() == first
        assert api.public_key() == first, "cached"
        assert key_file.read_bytes() == written, "never overwritten"

    def test_nothing_is_created_before_first_use(self, tmp_path: Path) -> None:
        key_file = tmp_path / "private_key.pem"

        WebPushSender(
            keys=VapidKeyStore(private_key_pem=None, key_file=key_file),
            subject="mailto:ops@example.com",
            ttl_seconds=60,
            timeout_seconds=1,
        )

        assert not key_file.exists()

    def test_configured_pem_wins_over_the_file(self, tmp_path: Path) -> None:
        configured = ec.generate_private_key(ec.SECP256R1())
        key_file = tmp_path / "private_key.pem"
        VapidKeyStore(private_key_pem=None, key_file=key_file).public_key()  # another key

        store = VapidKeyStore(private_key_pem=pem(configured), key_file=key_file)

        assert store.public_key() == application_server_key(configured)

    def test_configured_pem_creates_no_file(self, tmp_path: Path) -> None:
        key_file = tmp_path / "private_key.pem"
        configured = ec.generate_private_key(ec.SECP256R1())

        load_or_create_private_key(pem(configured), key_file)

        assert not key_file.exists()

    def test_losing_the_creation_race_reads_the_winners_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        key_file = tmp_path / "private_key.pem"
        winner = ec.generate_private_key(ec.SECP256R1())
        real_open = os.open

        def open_after_the_other_process(path: str, flags: int, mode: int = 0o777) -> int:
            Path(path).write_text(pem(winner))  # it wrote the file in the meantime
            return real_open(path, flags, mode)

        monkeypatch.setattr(os, "open", open_after_the_other_process)  # what O_EXCL guards

        key = load_or_create_private_key(None, key_file)

        assert application_server_key(key) == application_server_key(winner)

    @pytest.mark.parametrize(
        "configured",
        [
            "not a pem at all",
            "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n",
            pem(ec.generate_private_key(ec.SECP384R1())),
            pem(rsa.generate_private_key(public_exponent=65537, key_size=2048)),
        ],
        ids=["garbage", "broken-pem", "p384", "rsa"],
    )
    def test_rejects_a_configured_key_that_is_not_p256(
        self, tmp_path: Path, configured: str
    ) -> None:
        store = VapidKeyStore(private_key_pem=configured, key_file=tmp_path / "unused.pem")

        with pytest.raises(InvalidVapidKeyError):
            store.public_key()

    def test_rejects_a_corrupted_key_file(self, tmp_path: Path) -> None:
        key_file = tmp_path / "private_key.pem"
        key_file.write_text("corrupted")
        store = VapidKeyStore(private_key_pem=None, key_file=key_file)

        with pytest.raises(InvalidVapidKeyError):
            store.public_key()
        assert key_file.read_text() == "corrupted", "a broken key is never replaced silently"
