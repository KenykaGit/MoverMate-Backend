"""SQL fragments for CHECK constraints derived from domain enums.

Enums are stored as VARCHAR + CHECK rather than native Postgres enums: adding
a value is then an ordinary constraint change, not ALTER TYPE (docs/db-design.md).
"""

from collections.abc import Iterable
from enum import StrEnum

ENUM_LENGTH = 32


def one_of(column: str, values: Iterable[StrEnum]) -> str:
    """`column IN ('A', 'B', ...)` for a CheckConstraint; values are domain constants."""
    listed = ", ".join(f"'{value.value}'" for value in values)
    return f"{column} IN ({listed})"
