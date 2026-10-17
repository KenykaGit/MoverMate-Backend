"""Normalization shared by free-text value objects (names, titles, addresses)."""

import unicodedata

from app.domain.common.errors import DomainError


def bounded_text(
    raw: str, *, max_length: int, what: str, error: type[DomainError], min_length: int = 1
) -> str:
    """`raw` trimmed and in NFC, checked to be `min_length`..`max_length` characters long.

    NFC makes visually equal strings equal, which unique names and search rely on.
    """
    text = unicodedata.normalize("NFC", raw.strip())
    if not min_length <= len(text) <= max_length:
        raise error(f"{what} must be between {min_length} and {max_length} characters long")
    return text
