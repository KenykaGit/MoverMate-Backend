from uuid import UUID

from app.application.interfaces.project_reader import AbstractProjectReader, ProjectDTO
from app.domain.common.ids import UserId


class ListProjects:
    def __init__(self, reader: AbstractProjectReader) -> None:
        self._reader = reader

    async def __call__(self, user_id: UUID) -> list[ProjectDTO]:
        """Active projects the user is a member of, by moving date."""
        return await self._reader.list_for_member(UserId(user_id))
