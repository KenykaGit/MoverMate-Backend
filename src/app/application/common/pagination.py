from dataclasses import dataclass
from typing import Self

from app.application.exceptions import InvalidPaginationError

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True, kw_only=True)
class PageRequest:
    limit: int = DEFAULT_PAGE_SIZE
    offset: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= MAX_PAGE_SIZE:
            raise InvalidPaginationError(f"Limit must be between 1 and {MAX_PAGE_SIZE}")
        if self.offset < 0:
            raise InvalidPaginationError("Offset must not be negative")


@dataclass(frozen=True, slots=True, kw_only=True)
class Page[T]:
    items: tuple[T, ...]
    total: int
    limit: int
    offset: int

    @classmethod
    def of(cls, items: tuple[T, ...], *, total: int, request: PageRequest) -> Self:
        return cls(items=items, total=total, limit=request.limit, offset=request.offset)


@dataclass(frozen=True, slots=True, kw_only=True)
class CursorPageRequest:
    """Keyset pagination: `after` is the opaque cursor of the previous page."""

    limit: int = DEFAULT_PAGE_SIZE
    after: str | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= MAX_PAGE_SIZE:
            raise InvalidPaginationError(f"Limit must be between 1 and {MAX_PAGE_SIZE}")


@dataclass(frozen=True, slots=True, kw_only=True)
class CursorPage[T]:
    items: tuple[T, ...]
    next_cursor: str | None
    """None on the last page."""
