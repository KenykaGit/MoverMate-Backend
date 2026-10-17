from app.application.interfaces.push_keys import AbstractPushKeys


class GetVapidPublicKey:
    def __init__(self, keys: AbstractPushKeys) -> None:
        self._keys = keys

    async def __call__(self) -> str:
        """The key browsers pass to `pushManager.subscribe()` (base64url)."""
        return self._keys.public_key()
