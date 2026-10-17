"""A fake Web Push service over real HTTPS, and browsers that decrypt what it receives.

The sender under test runs unchanged: real aes128gcm encryption, real VAPID
signatures, a real aiohttp request over TLS. Only DNS is faked (FAKE_HOST
resolves to 127.0.0.1) and the client trusts the service's self-signed
certificate; both live in the aiohttp session handed to WebPushSender.
"""

import asyncio
import base64
import json
import os
import secrets
import socket
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any, Self

import aiohttp
import http_ece
from aiohttp import web
from aiohttp.abc import AbstractResolver, ResolveResult
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from dishka import Provider, Scope, provide
from multidict import CIMultiDict

from app.application.interfaces.push_sender import AbstractPushSender
from app.config import PushSettings
from app.domain.common.ids import UserId
from app.domain.notifications.push_subscription import PushEndpoint, PushKeys, PushSubscription
from app.infrastructure.push.sender import WebPushSender
from app.infrastructure.push.vapid import VapidKeyStore

FAKE_HOST = "push.test"
_LOOPBACK = "127.0.0.1"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(frozen=True, slots=True)
class Browser:
    """The receiving side of a subscription: the keys a browser keeps to decrypt."""

    endpoint: str
    private_key: ec.EllipticCurvePrivateKey = field(repr=False)
    auth_secret: bytes = field(repr=False)

    @classmethod
    def create(cls, endpoint: str) -> Self:
        return cls(
            endpoint=endpoint,
            private_key=ec.generate_private_key(ec.SECP256R1()),
            auth_secret=os.urandom(16),
        )

    @property
    def p256dh(self) -> str:
        return b64url(
            self.private_key.public_key().public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
            )
        )

    @property
    def auth(self) -> str:
        return b64url(self.auth_secret)

    @property
    def path(self) -> str:
        return "/" + self.endpoint.split("/", 3)[3]

    def to_json(self) -> dict[str, Any]:
        """What `PushSubscription.toJSON()` returns in the browser."""
        return {
            "endpoint": self.endpoint,
            "expirationTime": None,
            "keys": {"p256dh": self.p256dh, "auth": self.auth},
        }

    def subscription(self, user_id: UserId) -> PushSubscription:
        return PushSubscription.subscribe(
            user_id=user_id,
            endpoint=PushEndpoint(self.endpoint),
            keys=PushKeys(p256dh=self.p256dh, auth=self.auth),
        )

    def decrypt(self, body: bytes) -> dict[str, Any]:
        plain: bytes = http_ece.decrypt(
            body, private_key=self.private_key, auth_secret=self.auth_secret, version="aes128gcm"
        )
        payload: dict[str, Any] = json.loads(plain)
        return payload


@dataclass(frozen=True, slots=True)
class ReceivedPush:
    path: str
    headers: Mapping[str, str]
    body: bytes


class _FakeHostResolver(AbstractResolver):
    """Every name is the local machine: tests never reach a real push service."""

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,  # noqa: ARG002  # the base signature
    ) -> list[ResolveResult]:
        return [
            ResolveResult(
                hostname=host,
                host=_LOOPBACK,
                port=port,
                family=socket.AF_INET,
                proto=0,
                flags=socket.AI_NUMERICHOST,
            )
        ]

    async def close(self) -> None:
        return None


class FakePushService:
    """Records every message and answers 201, unless told otherwise per browser."""

    def __init__(self, workdir: Path) -> None:
        self._workdir = workdir
        self._cert_pem = b""
        self._runner: web.AppRunner | None = None
        self._port = 0
        self._browsers: dict[str, Browser] = {}
        self._statuses: dict[str, int] = {}
        self._hanging: set[str] = set()
        self._released = asyncio.Event()
        self._pairs = False
        self._closing = False
        self._arrived = asyncio.Condition()
        self.received: list[ReceivedPush] = []

    @property
    def origin(self) -> str:
        return f"https://{FAKE_HOST}:{self._port}"

    @property
    def port(self) -> int:
        return self._port

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/{tail:.*}", self._handle)
        self._runner = web.AppRunner(app, shutdown_timeout=1)
        await self._runner.setup()
        site = web.TCPSite(self._runner, _LOOPBACK, 0, ssl_context=self._server_tls())
        await site.start()
        server = site._server  # the only way to learn the bound port
        assert isinstance(server, asyncio.Server)
        self._port = server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        self._released.set()  # let hanging requests finish
        async with self._arrived:
            self._closing = True
            self._arrived.notify_all()
        if self._runner is not None:
            await self._runner.cleanup()

    def client_session(self) -> aiohttp.ClientSession:
        """Trusts only this service's certificate and resolves every host to it."""
        tls = ssl.create_default_context(cadata=self._cert_pem.decode())
        connector = aiohttp.TCPConnector(ssl=tls, resolver=_FakeHostResolver())
        return aiohttp.ClientSession(connector=connector)

    def browser(self) -> Browser:
        """A new browser subscribed at this service."""
        browser = Browser.create(f"{self.origin}/push/{secrets.token_urlsafe(16)}")
        self._browsers[browser.path] = browser
        return browser

    def answer(self, browser: Browser, status: int) -> None:
        """Answer this browser's messages with `status` from now on."""
        self._statuses[browser.path] = status

    def hang(self, browser: Browser) -> None:
        """Never answer this browser's messages (until the service stops)."""
        self._hanging.add(browser.path)

    def received_by(self, browser: Browser) -> list[ReceivedPush]:
        return [push for push in self.received if push.path == browser.path]

    def payloads(self, browser: Browser) -> list[dict[str, Any]]:
        return [browser.decrypt(push.body) for push in self.received_by(browser)]

    async def wait_for(self, count: int, *, within_seconds: float = 5.0) -> None:
        """Until at least `count` messages have arrived."""
        async with asyncio.timeout(within_seconds), self._arrived:
            await self._arrived.wait_for(lambda: len(self.received) >= count)

    def answer_in_pairs(self) -> None:
        """Hold every message until another one has arrived too.

        Two senders that really work in parallel pass; one that waits for the
        other (a lock) gets stuck until its timeout, and the test fails.
        """
        self._pairs = True

    async def _handle(self, request: web.Request) -> web.Response:
        push = ReceivedPush(request.path, CIMultiDict(request.headers), await request.read())
        async with self._arrived:
            self.received.append(push)
            self._arrived.notify_all()
            if self._pairs:
                pair_complete = (len(self.received) + 1) // 2 * 2  # 1, 2 -> 2; 3, 4 -> 4
                await self._arrived.wait_for(
                    lambda: len(self.received) >= pair_complete or self._closing
                )
        if request.path in self._hanging:
            await self._released.wait()
        return web.Response(status=self._statuses.get(request.path, 201))

    def _server_tls(self) -> ssl.SSLContext:
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, FAKE_HOST)])
        now = datetime.now(UTC)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.DNSName(FAKE_HOST), x509.IPAddress(IPv4Address(_LOOPBACK))]
                ),
                critical=False,
            )
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        self._cert_pem = certificate.public_bytes(serialization.Encoding.PEM)
        cert_file, key_file = self._workdir / "cert.pem", self._workdir / "key.pem"
        cert_file.write_bytes(self._cert_pem)
        key_file.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        tls.load_cert_chain(cert_file, key_file)
        return tls


def web_push_sender(
    keys: VapidKeyStore,
    session: aiohttp.ClientSession,
    *,
    ttl_seconds: int = 60,
    timeout_seconds: float = 5.0,
) -> WebPushSender:
    return WebPushSender(
        keys=keys,
        subject="mailto:ops@example.com",
        ttl_seconds=ttl_seconds,
        timeout_seconds=timeout_seconds,
        session=session,
    )


class FakePushServiceProvider(Provider):
    """Replaces the production sender: the same class, but its HTTP session
    reaches the fake push service."""

    scope = Scope.APP

    def __init__(self, session: aiohttp.ClientSession) -> None:
        super().__init__()
        self._session = session

    @provide(override=True)
    def push_sender(self, config: PushSettings, keys: VapidKeyStore) -> AbstractPushSender:
        return WebPushSender(
            keys=keys,
            subject=config.vapid_subject,
            ttl_seconds=config.ttl_seconds,
            timeout_seconds=config.timeout_seconds,
            session=self._session,
        )


def vapid_token(authorization: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    """The JWT header, the claims and the public key of a `vapid t=…,k=…` header."""
    scheme, _, params = authorization.partition(" ")
    assert scheme == "vapid", authorization
    values = dict(part.strip().split("=", 1) for part in params.split(","))
    header, claims, _ = values["t"].split(".")
    return json.loads(b64url_decode(header)), json.loads(b64url_decode(claims)), values["k"]
