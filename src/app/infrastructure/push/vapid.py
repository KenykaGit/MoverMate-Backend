"""VAPID keys (RFC 8292): the P-256 key pair that identifies this server to push services.

Browsers subscribe with the public key, so the pair must stay the same: losing
it silently breaks every subscription (push services answer 403).
"""

import base64
import os
import threading
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.application.interfaces.push_keys import AbstractPushKeys


class InvalidVapidKeyError(Exception):
    """The configured VAPID key is not a P-256 private key in PEM."""


class VapidKeyStore(AbstractPushKeys):
    """The key pair of the process, loaded (or created) on first use and then cached.

    The api and the worker share it through a volume: the api hands the public
    key to browsers, the worker signs with the private one.
    """

    def __init__(self, *, private_key_pem: str | None, key_file: Path) -> None:
        self._pem = private_key_pem
        self._key_file = key_file
        self._key: ec.EllipticCurvePrivateKey | None = None
        self._lock = threading.Lock()

    def private_key(self) -> ec.EllipticCurvePrivateKey:
        with self._lock:
            if self._key is None:
                self._key = load_or_create_private_key(self._pem, self._key_file)
            return self._key

    def public_key(self) -> str:
        return application_server_key(self.private_key())


def load_or_create_private_key(pem: str | None, key_file: Path) -> ec.EllipticCurvePrivateKey:
    """The configured key; else the one in `key_file`, created there on first use.

    Creation is exclusive (O_EXCL): of two processes starting at once, one writes
    the file and the other reads it.
    """
    if pem is not None:
        return _parse(pem.encode())
    try:
        return _parse(key_file.read_bytes())
    except FileNotFoundError:
        pass
    key = ec.generate_private_key(ec.SECP256R1())
    key_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _parse(key_file.read_bytes())
    with os.fdopen(descriptor, "wb") as file:
        file.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    return key


def application_server_key(key: ec.EllipticCurvePrivateKey) -> str:
    """The public key as browsers expect it in `pushManager.subscribe()` (base64url)."""
    raw = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _parse(pem: bytes) -> ec.EllipticCurvePrivateKey:
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except ValueError as error:
        raise InvalidVapidKeyError("The VAPID key is not a PEM private key") from error
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise InvalidVapidKeyError("The VAPID key must be an EC key on the P-256 curve")
    return key
