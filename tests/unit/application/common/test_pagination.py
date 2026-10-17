from app.application.common.pagination import MAX_PAGE_SIZE, Page, PageRequest
from app.application.exceptions import InvalidPaginationError
from tests.checks import not_rejected


def test_limit_and_offset_bounds() -> None:
    bounds = [(1, 0), (MAX_PAGE_SIZE, 10_000)]
    outside = [(0, 0), (MAX_PAGE_SIZE + 1, 0), (10, -1)]

    def request(page: tuple[int, int]) -> PageRequest:
        return PageRequest(limit=page[0], offset=page[1])

    assert [(r.limit, r.offset) for r in map(request, bounds)] == bounds
    assert not_rejected((request, page, InvalidPaginationError) for page in outside) == []


def test_page_echoes_the_request() -> None:
    page = Page.of(("a", "b"), total=7, request=PageRequest(limit=2, offset=4))

    assert (page.items, page.total, page.limit, page.offset) == (("a", "b"), 7, 2, 4)
