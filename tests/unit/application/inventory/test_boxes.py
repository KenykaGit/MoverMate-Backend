from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.application.exceptions import (
    BoxNotFoundError,
    BoxNotPackingError,
    BoxStatusConflictError,
    NotAuthenticatedError,
    NotBoxCreatorError,
    ProjectNotFoundError,
    ReminderInThePastError,
    RoomNotFoundError,
)
from app.application.inventory.cancel_contents_reminder import CancelContentsReminder
from app.application.inventory.change_box_status import ChangeBoxStatus
from app.application.inventory.create_box import CreateBox
from app.application.inventory.delete_box import DeleteBox
from app.application.inventory.dto import (
    BoxDTO,
    CancelContentsReminderDTO,
    ChangeBoxStatusDTO,
    CreateBoxDTO,
    DeleteBoxDTO,
    GetBoxByQrDTO,
    NewItemEntryDTO,
    NewItemsDTO,
    QrAccessLevel,
    ScheduleContentsReminderDTO,
    UpdateBoxDTO,
)
from app.application.inventory.get_box_by_qr import GetBoxByQr
from app.application.inventory.schedule_contents_reminder import ScheduleContentsReminder
from app.application.inventory.update_box import UpdateBox
from app.domain.audit.entity import AuditAction
from app.domain.common.ids import BoxId
from app.domain.inventory.box import BoxStatus
from app.domain.inventory.errors import BoxStatusTransitionError, WrongRoomKindError
from app.domain.inventory.item import ItemState
from app.domain.inventory.value_objects import Priority
from app.domain.projects.value_objects import QrCodeStrategy
from tests.unit.application.inventory.conftest import Preset, other_project


async def create_box(
    preset: Preset,
    *,
    user_id: UUID | None = None,
    title: str | None = None,
    is_fragile: bool = False,
    items: NewItemsDTO | None = None,
) -> BoxDTO:
    return await preset.make(CreateBox)(
        CreateBoxDTO(
            user_id=user_id or preset.owner.id,
            project_id=preset.project.id,
            destination_room_id=preset.destination.id,
            title=title,
            is_fragile=is_fragile,
            items=items,
        )
    )


def soon() -> datetime:
    return datetime.now(UTC) + timedelta(hours=2)


class TestCreateBox:
    async def test_new_box_is_packing_under_the_project_lock(self, preset: Preset) -> None:
        box = await create_box(preset, title="Посуда", is_fragile=True)

        assert (box.number, box.status, box.title, box.is_fragile) == (
            1,
            BoxStatus.PACKING,
            "Посуда",
            True,
        )
        assert box.created_by.username == "owner"
        assert preset.projects.numbering_locks == [preset.project.id]
        assert preset.audit_log.actions() == [AuditAction.BOX_CREATED]
        assert preset.uow.commits == 1

    async def test_items_are_packed_into_it(self, preset: Preset) -> None:
        box = await create_box(
            preset,
            items=NewItemsDTO(
                source_room_id=preset.source.id,
                priority=Priority.HIGH,
                entries=(
                    NewItemEntryDTO(name="Кружки", quantity=6),
                    NewItemEntryDTO(name="Чайник"),
                ),
            ),
        )

        stored = list(preset.items.items.values())
        assert {(i.state, i.box_id, i.priority) for i in stored} == {
            (ItemState.IN_BOX, box.id, Priority.HIGH)
        }
        assert box.item_count == 2
        assert box.status is BoxStatus.PACKING

    async def test_takes_the_lowest_free_number(self, preset: Preset) -> None:
        first, second = await create_box(preset), await create_box(preset)
        await preset.make(DeleteBox)(DeleteBoxDTO(user_id=preset.owner.id, box_id=first.id))

        third = await create_box(preset)

        assert (second.number, third.number) == (2, 1)

    async def test_the_items_need_a_source_room(self, preset: Preset) -> None:
        with pytest.raises(WrongRoomKindError):
            await create_box(
                preset,
                items=NewItemsDTO(
                    source_room_id=preset.destination.id,
                    entries=(NewItemEntryDTO(name="Кружки"),),
                ),
            )
        assert preset.boxes.boxes == {}
        assert preset.uow.commits == 0

    async def test_rooms_of_other_projects_are_not_found(self, preset: Preset) -> None:
        other = other_project(preset)
        preset.rooms.rooms[preset.destination.id].project_id = other.id

        with pytest.raises(RoomNotFoundError):
            await create_box(preset)

    async def test_strangers_cannot_create(self, preset: Preset) -> None:
        with pytest.raises(ProjectNotFoundError):
            await create_box(preset, user_id=preset.stranger.id)


class TestChangeBoxStatus:
    async def test_one_step_is_recorded(self, preset: Preset) -> None:
        box = await create_box(preset)
        change = preset.make(ChangeBoxStatus)

        packed = await change(
            ChangeBoxStatusDTO(user_id=preset.member.id, box_id=box.id, status=BoxStatus.PACKED)
        )
        rolled_back = await change(
            ChangeBoxStatusDTO(user_id=preset.member.id, box_id=box.id, status=BoxStatus.PACKING)
        )

        assert (packed.status, rolled_back.status) == (BoxStatus.PACKED, BoxStatus.PACKING)
        assert preset.audit_log.actions()[1:] == [
            AuditAction.BOX_STATUS_CHANGED,
            AuditAction.BOX_STATUS_ROLLED_BACK,
        ]

    async def test_the_current_status_again_changes_nothing(self, preset: Preset) -> None:
        box = await create_box(preset)
        preset.boxes.conflict = True  # would fail if anything were written

        same = await preset.make(ChangeBoxStatus)(
            ChangeBoxStatusDTO(user_id=preset.owner.id, box_id=box.id, status=BoxStatus.PACKING)
        )

        assert same.status is BoxStatus.PACKING
        assert preset.audit_log.actions() == [AuditAction.BOX_CREATED]

    async def test_a_skipped_step_is_rejected(self, preset: Preset) -> None:
        box = await create_box(preset)

        with pytest.raises(BoxStatusTransitionError):
            await preset.make(ChangeBoxStatus)(
                ChangeBoxStatusDTO(user_id=preset.owner.id, box_id=box.id, status=BoxStatus.LOADED)
            )

    async def test_a_concurrent_change_is_a_conflict(self, preset: Preset) -> None:
        box = await create_box(preset)
        preset.boxes.conflict = True

        with pytest.raises(BoxStatusConflictError):
            await preset.make(ChangeBoxStatus)(
                ChangeBoxStatusDTO(user_id=preset.owner.id, box_id=box.id, status=BoxStatus.PACKED)
            )
        assert preset.uow.commits == 1  # only the creation

    async def test_leaving_packing_cancels_the_contents_reminder(self, preset: Preset) -> None:
        box = await create_box(preset)
        await preset.make(ScheduleContentsReminder)(
            ScheduleContentsReminderDTO(user_id=preset.owner.id, box_id=box.id, remind_at=soon())
        )

        packed = await preset.make(ChangeBoxStatus)(
            ChangeBoxStatusDTO(user_id=preset.owner.id, box_id=box.id, status=BoxStatus.PACKED)
        )

        assert preset.notifications.pending() == []
        assert packed.contents_reminder_at is None


class TestUpdateBox:
    async def test_changes_only_what_was_sent(self, preset: Preset) -> None:
        box = await create_box(preset, title="Книги", is_fragile=True)

        updated = await preset.make(UpdateBox)(
            UpdateBoxDTO(user_id=preset.owner.id, box_id=box.id, title=None)
        )

        assert (updated.title, updated.is_fragile) == (None, True)

    async def test_a_new_destination_must_be_a_destination_room(self, preset: Preset) -> None:
        box = await create_box(preset)

        with pytest.raises(WrongRoomKindError):
            await preset.make(UpdateBox)(
                UpdateBoxDTO(
                    user_id=preset.owner.id, box_id=box.id, destination_room_id=preset.source.id
                )
            )


async def test_delete_box_takes_items_and_reminders_along(preset: Preset) -> None:
    box = await create_box(
        preset,
        items=NewItemsDTO(source_room_id=preset.source.id, entries=(NewItemEntryDTO(name="Ваза"),)),
    )
    await preset.make(ScheduleContentsReminder)(
        ScheduleContentsReminderDTO(user_id=preset.owner.id, box_id=box.id, remind_at=soon())
    )

    await preset.make(DeleteBox)(DeleteBoxDTO(user_id=preset.member.id, box_id=box.id))

    assert not preset.boxes.boxes[BoxId(box.id)].is_active
    assert [item.is_active for item in preset.items.items.values()] == [False]
    assert preset.notifications.pending() == []
    assert preset.audit_log.actions()[-1] is AuditAction.BOX_DELETED
    deleted = preset.audit_log.entries[-1]
    assert deleted.details == {"number": "1", "items": "1"}


class TestContentsReminder:
    async def test_the_creator_sets_it_and_a_new_one_replaces_it(self, preset: Preset) -> None:
        box = await create_box(preset)
        schedule = preset.make(ScheduleContentsReminder)
        later = soon() + timedelta(days=1)

        await schedule(
            ScheduleContentsReminderDTO(user_id=preset.owner.id, box_id=box.id, remind_at=soon())
        )
        view = await schedule(
            ScheduleContentsReminderDTO(user_id=preset.owner.id, box_id=box.id, remind_at=later)
        )

        assert [n.scheduled_at for n in preset.notifications.pending()] == [later]
        assert view.contents_reminder_at == later

    @pytest.mark.parametrize(
        ("who", "packed", "remind_in", "error"),
        [
            ("member", False, timedelta(hours=1), NotBoxCreatorError),
            ("owner", True, timedelta(hours=1), BoxNotPackingError),
            ("owner", False, -timedelta(minutes=1), ReminderInThePastError),
            ("stranger", False, timedelta(hours=1), BoxNotFoundError),
        ],
    )
    async def test_rules(
        self,
        preset: Preset,
        who: str,
        packed: bool,
        remind_in: timedelta,
        error: type[Exception],
    ) -> None:
        box = await create_box(preset)
        if packed:
            await preset.make(ChangeBoxStatus)(
                ChangeBoxStatusDTO(user_id=preset.owner.id, box_id=box.id, status=BoxStatus.PACKED)
            )
        user = {"owner": preset.owner, "member": preset.member, "stranger": preset.stranger}[who]

        with pytest.raises(error):
            await preset.make(ScheduleContentsReminder)(
                ScheduleContentsReminderDTO(
                    user_id=user.id, box_id=box.id, remind_at=datetime.now(UTC) + remind_in
                )
            )
        assert preset.notifications.pending() == []

    async def test_cancel_is_for_the_creator_and_idempotent(self, preset: Preset) -> None:
        box = await create_box(preset)
        cancel = preset.make(CancelContentsReminder)
        await preset.make(ScheduleContentsReminder)(
            ScheduleContentsReminderDTO(user_id=preset.owner.id, box_id=box.id, remind_at=soon())
        )

        with pytest.raises(NotBoxCreatorError):
            await cancel(CancelContentsReminderDTO(user_id=preset.member.id, box_id=box.id))
        await cancel(CancelContentsReminderDTO(user_id=preset.owner.id, box_id=box.id))
        await cancel(CancelContentsReminderDTO(user_id=preset.owner.id, box_id=box.id))

        assert preset.notifications.pending() == []


class TestGetBoxByQr:
    @pytest.mark.parametrize(
        ("strategy", "who", "expected"),
        [
            (QrCodeStrategy.PROTECTED, "anonymous", NotAuthenticatedError),
            (QrCodeStrategy.PROTECTED, "stranger", BoxNotFoundError),
            (QrCodeStrategy.PROTECTED, "member", QrAccessLevel.WRITE),
            (QrCodeStrategy.OPEN, "anonymous", QrAccessLevel.READ),
            (QrCodeStrategy.OPEN, "stranger", QrAccessLevel.READ),
            (QrCodeStrategy.OPEN, "member", QrAccessLevel.WRITE),
        ],
    )
    async def test_access_matrix(
        self,
        preset: Preset,
        strategy: QrCodeStrategy,
        who: str,
        expected: QrAccessLevel | type[Exception],
    ) -> None:
        preset.projects.projects[preset.project.id].change_qr_code_strategy(strategy)
        box = await create_box(preset)
        user_id = {"anonymous": None, "stranger": preset.stranger.id, "member": preset.member.id}[
            who
        ]
        get = preset.make(GetBoxByQr)
        data = GetBoxByQrDTO(user_id=user_id, qr_token=box.qr_token)

        if isinstance(expected, QrAccessLevel):
            found = await get(data)
            assert (found.access, found.box.id, found.project.name) == (
                expected,
                box.id,
                "Moving to Kazan",
            )
        else:
            with pytest.raises(expected):
                await get(data)

    @pytest.mark.parametrize("token", ["short", "AAAAAAAAAAAAAAAAAAAAAA"])
    async def test_malformed_and_unknown_tokens_are_not_found(
        self, preset: Preset, token: str
    ) -> None:
        with pytest.raises(BoxNotFoundError):
            await preset.make(GetBoxByQr)(GetBoxByQrDTO(user_id=preset.owner.id, qr_token=token))

    async def test_a_box_of_a_deleted_project_is_not_found(self, preset: Preset) -> None:
        box = await create_box(preset)
        preset.projects.projects[preset.project.id].soft_delete(by=preset.owner.id)

        with pytest.raises(BoxNotFoundError):
            await preset.make(GetBoxByQr)(
                GetBoxByQrDTO(user_id=preset.owner.id, qr_token=box.qr_token)
            )
