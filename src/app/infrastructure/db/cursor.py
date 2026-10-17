"""Opaque keyset cursors: the sort key of the last row of a page, as base64url JSON.

Clients pass `next_cursor` back as is; its content is not part of the contract.
"""

import base64
import binascii
import json
from typing import Any

from app.application.exceptions import InvalidPaginationError


def encode_cursor(key: list[Any]) -> str:
    raw = json.dumps(key, separators=(",", ":"), ensure_ascii=False).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str, *, length: int) -> list[Any]:
    """The key packed by encode_cursor.

    Raises:
        InvalidPaginationError: not a cursor of this list.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        key = json.loads(raw)
    except (binascii.Error, UnicodeDecodeError, ValueError) as error:
        raise InvalidPaginationError("Malformed cursor") from error
    if not isinstance(key, list) or len(key) != length:
        raise InvalidPaginationError("Malformed cursor")
    return key
