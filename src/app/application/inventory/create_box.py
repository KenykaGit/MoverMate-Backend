import logging

from app.application.common.clock import utc_now
from app.application.common.membership import project_of_member
from app.application.common.uow import AbstractUnitOfWork
from app.application.interfaces.audit_log_repository import AbstractAuditLogRepository
from app.application.interfaces.box_repository import AbstractBoxRepository
from app.application.interfaces.item_repository import AbstractItemRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.room_repository import AbstractRoomRepository
from app.application.inventory.dto import BoxDTO, CreateBoxDTO
from app.application.inventory.lookup import box_view, room_in_project
from app.application.inventory.read_model import AbstractInventoryReadModel
from app.application.inventory.services import build_items
from app.domain.audit.entity import AuditAction, AuditEntry
from app.domain.common.ids import UserId
from app.domain.inventory.box import Box
from app.domain.inventory.value_objects import BoxTitle

logger = logging.getLogger(__name__)


class CreateBox:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        rooms: AbstractRoomRepository,
        boxes: AbstractBoxRepository,
        items: AbstractItemRepository,
        audit_log: AbstractAuditLogRepository,
        read_model: AbstractInventoryReadModel,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._rooms = rooms
        self._boxes = boxes
        self._items = items
        self._audit_log = audit_log
        self._read_model = read_model
        self._uow = uow

    async def __call__(self, data: CreateBoxDTO) -> BoxDTO:
        """A new box in PACKING with the lowest number free among active boxes.

        Numbering is serialized per project (a lock on the project row). Items, if
        given, are created IN_BOX in the same transaction; the box stays PACKING.

        Raises:
            ProjectNotFoundError: no such active project, or the caller is not a member.
            RoomNotFoundError: no such active room in the project.
            WrongRoomKindError: the destination is not a DESTINATION room, or the
                source of the items is not a SOURCE one.
            InvalidBoxTitleError, InvalidItemNameError, InvalidQuantityError: bad values.
        """
        title = None if data.title is None else BoxTitle(data.title)
        project = await project_of_member(self._projects, data.project_id, data.user_id)
        destination = await room_in_project(self._rooms, data.destination_room_id, project.id)
        source = (
            None
            if data.items is None
            else await room_in_project(self._rooms, data.items.source_room_id, project.id)
        )
        now = utc_now()

        await self._projects.lock_for_box_numbering(project.id)
        box = Box.create(
            project_id=project.id,
            number=await self._boxes.next_free_number(project.id),
            destination=destination,
            created_by=UserId(data.user_id),
            now=now,
            title=title,
            is_fragile=data.is_fragile,
        )
        items = (
            []
            if data.items is None or source is None
            else build_items(
                project_id=project.id,
                source=source,
                entries=data.items.entries,
                priority=data.items.priority,
                box=box,
            )
        )
        await self._boxes.add(box)
        await self._items.add_many(items)
        await self._audit_log.add(
            AuditEntry.record(
                project_id=project.id,
                actor_id=UserId(data.user_id),
                action=AuditAction.BOX_CREATED,
                entity_id=box.id,
                now=now,
                details={"number": str(box.number), "items": str(len(items))},
            )
        )
        view = await box_view(self._read_model, box.id, data.user_id)
        await self._uow.commit()

        logger.info(
            "box_created",
            extra={
                "project_id": str(box.project_id),
                "box_id": str(box.id),
                "number": box.number.value,
                "items": len(items),
                "user_id": str(data.user_id),
            },
        )
        return view
