from abc import ABC, abstractmethod


class AbstractPushKeys(ABC):
    """The VAPID key pair (RFC 8292) that identifies this server to push services."""

    @abstractmethod
    def public_key(self) -> str:
        """The application server key for `pushManager.subscribe()`: base64url of
        the uncompressed P-256 point. Stable: browsers subscribe with it."""
