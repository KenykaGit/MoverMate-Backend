import logging
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from app.application.common.clock import utc_now
from app.application.common.pagination import MAX_PAGE_SIZE, CursorPageRequest
from app.application.exceptions import UsernameAlreadyTakenError
from app.application.inventory.change_box_status import ChangeBoxStatus
from app.application.inventory.change_item_state import ChangeItemState
from app.application.inventory.create_box import CreateBox
from app.application.inventory.create_items import CreateItems
from app.application.inventory.dto import (
    BoxDTO,
    ChangeBoxStatusDTO,
    ChangeItemStateDTO,
    CreateBoxDTO,
    CreateItemsDTO,
    ListItemsDTO,
    ListRoomsDTO,
    NewItemEntryDTO,
    NewItemsDTO,
)
from app.application.inventory.list_items import ListItems
from app.application.inventory.list_rooms import ListRooms
from app.application.projects.create_project import CreateProject
from app.application.projects.dto import CreateProjectDTO
from app.application.seed.catalog import (
    TEST_USERS,
    SeedBox,
    SeedItem,
    SeedProject,
    SeedTask,
    SeedUnpackedItems,
)
from app.application.seed.dto import SeedTestUsersDTO, SeedTestUsersResultDTO
from app.application.tasks.complete_task import CompleteTask
from app.application.tasks.create_task import CreateTask
from app.application.tasks.dto import CreateTaskDTO, TaskActionDTO, TaskDTO
from app.application.users.dto import RegisterUserDTO
from app.application.users.register_user import RegisterUser
from app.domain.inventory.box import BoxStatus
from app.domain.inventory.item import ItemState
from app.domain.inventory.value_objects import RoomKind

logger = logging.getLogger(__name__)

DUE_TIME = time(12)
"""Tasks fall due at noon UTC of their day."""


class SeedTestUsers:
    """Fills an empty environment with accounts to try the product on."""

    def __init__(
        self,
        register_user: RegisterUser,
        create_project: CreateProject,
        list_rooms: ListRooms,
        create_box: CreateBox,
        create_items: CreateItems,
        create_task: CreateTask,
        change_box_status: ChangeBoxStatus,
        list_items: ListItems,
        change_item_state: ChangeItemState,
        complete_task: CompleteTask,
    ) -> None:
        self._register_user = register_user
        self._create_project = create_project
        self._list_rooms = list_rooms
        self._create_box = create_box
        self._create_items = create_items
        self._create_task = create_task
        self._change_box_status = change_box_status
        self._list_items = list_items
        self._change_item_state = change_item_state
        self._complete_task = complete_task

    async def __call__(self, data: SeedTestUsersDTO) -> SeedTestUsersResultDTO:
        """Register the test accounts and fill their projects, skipping the existing ones.

        Safe to run on every start, and from several processes at once: whoever
        registers a username first fills that account, the rest skip it. Every step
        commits on its own, so a crash in the middle leaves a half-filled account
        that the next run skips; drop the account to seed it again.

        Raises:
            DomainError: the catalog or the password breaks a domain rule.
        """
        created: list[str] = []
        skipped: list[str] = []
        for spec in TEST_USERS:
            try:
                user = await self._register_user(
                    RegisterUserDTO(username=spec.username, password=data.password)
                )
            except UsernameAlreadyTakenError:
                skipped.append(spec.username)
                continue
            for project in spec.projects:
                await self._seed_project(user.id, project)
            created.append(spec.username)

        logger.info("test_users_seeded", extra={"created": created, "skipped": skipped})
        return SeedTestUsersResultDTO(created=tuple(created), skipped=tuple(skipped))

    async def _seed_project(self, user_id: UUID, spec: SeedProject) -> None:
        moving_date = utc_now().date() + timedelta(days=spec.days_until_move)
        project = await self._create_project(
            CreateProjectDTO(
                owner_id=user_id,
                name=spec.name,
                moving_date=moving_date,
                time_zone=spec.time_zone,
                origin_address=spec.origin_address,
                destination_address=spec.destination_address,
                source_rooms=spec.source_rooms,
                destination_rooms=spec.destination_rooms,
            )
        )
        rooms = await self._list_rooms(ListRoomsDTO(user_id=user_id, project_id=project.id))
        room_ids = {(room.kind, room.name): room.id for room in rooms}

        boxes = [await self._seed_box(user_id, project.id, room_ids, box) for box in spec.boxes]
        for unpacked in spec.unpacked:
            await self._seed_unpacked(user_id, project.id, room_ids, unpacked)
        tasks = [
            await self._seed_task(user_id, project.id, moving_date, task) for task in spec.tasks
        ]
        if spec.is_finished:
            await self._finish_move(user_id, project.id, boxes, tasks)

    async def _seed_box(
        self,
        user_id: UUID,
        project_id: UUID,
        room_ids: dict[tuple[RoomKind, str], UUID],
        spec: SeedBox,
    ) -> BoxDTO:
        return await self._create_box(
            CreateBoxDTO(
                user_id=user_id,
                project_id=project_id,
                destination_room_id=room_ids[(RoomKind.DESTINATION, spec.destination_room)],
                title=spec.title,
                is_fragile=spec.is_fragile,
                items=NewItemsDTO(
                    source_room_id=room_ids[(RoomKind.SOURCE, spec.source_room)],
                    priority=spec.priority,
                    entries=_entries(spec.items),
                ),
            )
        )

    async def _seed_unpacked(
        self,
        user_id: UUID,
        project_id: UUID,
        room_ids: dict[tuple[RoomKind, str], UUID],
        spec: SeedUnpackedItems,
    ) -> None:
        await self._create_items(
            CreateItemsDTO(
                user_id=user_id,
                project_id=project_id,
                items=NewItemsDTO(
                    source_room_id=room_ids[(RoomKind.SOURCE, spec.source_room)],
                    priority=spec.priority,
                    entries=_entries(spec.items),
                ),
            )
        )

    async def _seed_task(
        self, user_id: UUID, project_id: UUID, moving_date: date, spec: SeedTask
    ) -> TaskDTO:
        due_at = (
            None
            if spec.days_from_move is None
            else datetime.combine(
                moving_date + timedelta(days=spec.days_from_move), DUE_TIME, tzinfo=UTC
            )
        )
        return await self._create_task(
            CreateTaskDTO(
                user_id=user_id,
                project_id=project_id,
                title=spec.title,
                description=spec.description,
                phase=spec.phase,
                due_at=due_at,
            )
        )

    async def _finish_move(
        self, user_id: UUID, project_id: UUID, boxes: list[BoxDTO], tasks: list[TaskDTO]
    ) -> None:
        """Walk the project to the end of the move, one step at a time as a user would."""
        item_ids: list[UUID] = []
        after: str | None = None
        while True:
            page = await self._list_items(
                ListItemsDTO(
                    user_id=user_id,
                    project_id=project_id,
                    page=CursorPageRequest(limit=MAX_PAGE_SIZE, after=after),
                )
            )
            item_ids.extend(item.id for item in page.items)
            after = page.next_cursor
            if after is None:
                break

        for box in boxes:
            for status in list(BoxStatus)[1:]:
                await self._change_box_status(
                    ChangeBoxStatusDTO(user_id=user_id, box_id=box.id, status=status)
                )
        for item_id in item_ids:
            await self._change_item_state(
                ChangeItemStateDTO(user_id=user_id, item_id=item_id, state=ItemState.TAKEN_OUT)
            )
        for task in tasks:
            await self._complete_task(TaskActionDTO(user_id=user_id, task_id=task.id))


def _entries(items: tuple[SeedItem, ...]) -> tuple[NewItemEntryDTO, ...]:
    return tuple(NewItemEntryDTO(name=item.name, quantity=item.quantity) for item in items)
