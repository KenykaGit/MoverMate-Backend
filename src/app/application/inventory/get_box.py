from app.application.interfaces.box_repository import AbstractBoxRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.inventory.dto import BoxDTO, GetBoxDTO
from app.application.inventory.lookup import box_of_member, box_view
from app.application.inventory.read_model import AbstractInventoryReadModel


class GetBox:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        boxes: AbstractBoxRepository,
        read_model: AbstractInventoryReadModel,
    ) -> None:
        self._projects = projects
        self._boxes = boxes
        self._read_model = read_model

    async def __call__(self, data: GetBoxDTO) -> BoxDTO:
        """The active box with its room, creator and a preview of its contents.

        Raises:
            BoxNotFoundError: no such active box in a project of the caller.
        """
        box = await box_of_member(self._boxes, self._projects, data.box_id, data.user_id)
        return await box_view(self._read_model, box.id, data.user_id)
