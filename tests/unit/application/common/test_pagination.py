import pytest

from app.application.common.pagination import MAX_PAGE_SIZE, Page, PageRequest
from app.application.exceptions import InvalidPaginationError


@pytest.mark.parametrize(("limit", "offset"), [(1, 0), (MAX_PAGE_SIZE, 10_000)])
def test_accepts_bounds(limit: int, offset: int) -> None:
    assert PageRequest(limit=limit, offset=offset).limit == limit


@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (MAX_PAGE_SIZE + 1, 0), (10, -1)])
def test_rejects_out_of_range(limit: int, offset: int) -> None:
    with pytest.raises(InvalidPaginationError):
        PageRequest(limit=limit, offset=offset)


def test_page_echoes_the_request() -> None:
    page = Page.of(("a", "b"), total=7, request=PageRequest(limit=2, offset=4))

    assert (page.items, page.total, page.limit, page.offset) == (("a", "b"), 7, 2, 4)
