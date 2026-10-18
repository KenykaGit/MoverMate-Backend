import logging

from app.application.common.patch import UNSET, sent_fields
from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.box_repository import AbstractBoxRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.room_repository import AbstractRoomRepository
from app.application.inventory.dto import BoxDTO, UpdateBoxDTO
from app.application.inventory.lookup import box_of_member, box_view, room_in_project
from app.application.inventory.read_model import AbstractInventoryReadModel
from app.domain.inventory.value_objects import BoxTitle

logger = logging.getLogger(__name__)


class UpdateBox:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        rooms: AbstractRoomRepository,
        boxes: AbstractBoxRepository,
        read_model: AbstractInventoryReadModel,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._rooms = rooms
        self._boxes = boxes
        self._read_model = read_model
        self._uow = uow

    async def __call__(self, data: UpdateBoxDTO) -> BoxDTO:
        """Change the title, the fragile flag or the destination room of a box.

        Raises:
            BoxNotFoundError: no such active box in a project of the caller.
            RoomNotFoundError: no such active room in the project.
            WrongRoomKindError: the new destination is not a DESTINATION room.
            InvalidBoxTitleError: the title is empty or too long.
        """
        title = data.title
        new_title = None if title is UNSET or title is None else BoxTitle(title)
        box = await box_of_member(self._boxes, self._projects, data.box_id, data.user_id)

        if title is not UNSET:
            box.change_title(new_title)
        if data.is_fragile is not UNSET:
            box.set_fragile(data.is_fragile)
        if data.destination_room_id is not UNSET:
            room = await room_in_project(self._rooms, data.destination_room_id, box.project_id)
            box.change_destination(room)

        await self._boxes.update(box)
        view = await box_view(self._read_model, box.id, data.user_id)
        await self._uow.commit()

        fields = sent_fields(data, "title", "is_fragile", "destination_room_id")
        if fields:
            logger.info(
                "box_updated",
                extra={
                    "project_id": str(box.project_id),
                    "box_id": str(box.id),
                    "number": box.number.value,
                    "user_id": str(data.user_id),
                    "fields": fields,
                },
            )
        return view
