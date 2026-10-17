"""Partial updates (PATCH): a field the client did not send stays as it is.

`None` cannot mean that, because it clears an optional field. A field of a patch
DTO is therefore `T | Unset`: UNSET leaves the value alone, anything else replaces it.
"""

from enum import Enum


class Unset(Enum):
    UNSET = "UNSET"

    def __repr__(self) -> str:
        return "UNSET"


UNSET = Unset.UNSET

type Patch[T] = T | Unset
