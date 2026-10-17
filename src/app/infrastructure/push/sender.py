import json
import time
from urllib.parse import urlsplit

import aiohttp
from py_vapid import Vapid
from pywebpush import WebPusher, WebPushException

from app.application.interfaces.push_sender import (
    AbstractPushSender,
    PushMessage,
    PushOutcome,
    PushResult,
)
from app.domain.notifications.push_subscription import PushSubscription
from app.infrastructure.push.vapid import VapidKeyStore

# VAPID tokens may live up to 24 h (RFC 8292 §2); a fresh one per message is cheap.
_TOKEN_LIFETIME_SECONDS = 12 * 60 * 60
_ACCEPTED = frozenset({200, 201, 202})
_GONE = frozenset({404, 410})


class WebPushSender(AbstractPushSender):
    """Web Push (RFC 8030) with aes128gcm payload encryption (RFC 8291) and VAPID.

    The key and the HTTP session are created on the first message: building the
    sender (e.g. in the api process, which never sends) has no side effects.
    """

    def __init__(
        self,
        *,
        keys: VapidKeyStore,
        subject: str,
        ttl_seconds: int,
        timeout_seconds: float,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._keys = keys
        self._subject = subject
        self._ttl = ttl_seconds
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session = session
        self._owns_session = session is None
        self._vapid: Vapid | None = None

    async def send(self, subscription: PushSubscription, message: PushMessage) -> PushResult:
        endpoint = subscription.endpoint.value
        payload = json.dumps(
            {"title": message.title, "body": message.body, "url": message.url, **message.data},
            ensure_ascii=False,
        )
        try:
            pusher = WebPusher(
                {
                    "endpoint": endpoint,
                    "keys": {"p256dh": subscription.keys.p256dh, "auth": subscription.keys.auth},
                },
                aiohttp_session=self._http(),
            )
            response = await pusher.send_async(
                payload,
                self._vapid_headers(endpoint) | {"Urgency": "normal"},
                ttl=self._ttl,
                content_encoding="aes128gcm",
                timeout=self._timeout,
            )
        except (WebPushException, ValueError) as error:
            # The browser's keys cannot encrypt anything: the subscription is unusable.
            return PushResult(PushOutcome.GONE, f"invalid subscription keys: {error}")
        except (aiohttp.ClientError, TimeoutError) as error:
            return PushResult(PushOutcome.FAILED, f"{_host(endpoint)}: {error!r}")

        status = response.status
        if status in _ACCEPTED:
            return PushResult(PushOutcome.DELIVERED)
        if status in _GONE:
            return PushResult(PushOutcome.GONE, f"{_host(endpoint)} answered {status}")
        return PushResult(PushOutcome.FAILED, f"{_host(endpoint)} answered {status}")

    async def aclose(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    def _vapid_headers(self, endpoint: str) -> dict[str, str]:
        if self._vapid is None:
            self._vapid = Vapid(self._keys.private_key())
        parts = urlsplit(endpoint)
        claims = {
            "sub": self._subject,
            "aud": f"{parts.scheme}://{parts.netloc}",
            "exp": int(time.time()) + _TOKEN_LIFETIME_SECONDS,
        }
        headers: dict[str, str] = self._vapid.sign(claims)
        return headers


def _host(endpoint: str) -> str:
    """For errors and logs: the full endpoint is a capability, like a password."""
    return urlsplit(endpoint).netloc
