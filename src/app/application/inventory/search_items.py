import re

from app.application.common.pagination import PageRequest
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.inventory.dto import ItemSearchDTO, SearchItemsDTO
from app.application.inventory.lookup import project_of_member
from app.application.inventory.read_model import AbstractInventoryReadModel

# Only word characters reach the search: no operator of the query language can.
_WORD = re.compile(r"\w+")


class SearchItems:
    def __init__(
        self, projects: AbstractProjectRepository, read_model: AbstractInventoryReadModel
    ) -> None:
        self._projects = projects
        self._read_model = read_model

    async def __call__(self, data: SearchItemsDTO) -> ItemSearchDTO:
        """Full-text search of active items by name, words matched by prefix, ranked.

        An empty query finds nothing. Paged by offset: the order is by rank.

        Raises:
            InvalidPaginationError: the limit or offset is out of range.
            ProjectNotFoundError: no such active project, or the caller is not a member.
        """
        page = PageRequest(limit=data.limit, offset=data.offset)
        project = await project_of_member(self._projects, data.project_id, data.user_id)

        words = _WORD.findall(data.query.lower())
        if not words:
            return ItemSearchDTO(items=(), has_more=False)
        return await self._read_model.search(
            project.id, words, limit=page.limit, offset=page.offset
        )
