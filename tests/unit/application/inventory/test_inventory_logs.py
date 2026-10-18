"""Audit events of rooms, items and box reminders: who changed what, by name."""

from datetime import timedelta

from app.application.common.clock import utc_now
from app.application.inventory.cancel_contents_reminder import CancelContentsReminder
from app.application.inventory.create_box import CreateBox
from app.application.inventory.create_item import CreateItem
from app.application.inventory.create_items import CreateItems
from app.application.inventory.create_room import CreateRoom
from app.application.inventory.delete_item import DeleteItem
from app.application.inventory.dto import (
    CancelContentsReminderDTO,
    CreateBoxDTO,
    CreateItemDTO,
    CreateItemsDTO,
    CreateRoomDTO,
    DeleteItemDTO,
    NewItemEntryDTO,
    NewItemsDTO,
    RenameRoomDTO,
    ScheduleContentsReminderDTO,
    UpdateBoxDTO,
    UpdateItemDTO,
)
from app.application.inventory.rename_room import RenameRoom
from app.application.inventory.schedule_contents_reminder import ScheduleContentsReminder
from app.application.inventory.update_box import UpdateBox
from app.application.inventory.update_item import UpdateItem
from app.domain.inventory.value_objects import RoomKind
from tests.conftest import LogEvents
from tests.unit.application.inventory.conftest import Preset


def events(log_events: LogEvents, name: str) -> list[dict[str, object]]:
    return [e for e in log_events() if e["event"] == name]


async def test_room_created_and_renamed(preset: Preset, log_events: LogEvents) -> None:
    room = await preset.make(CreateRoom)(
        CreateRoomDTO(
            user_id=preset.member.id,
            project_id=preset.project.id,
            kind=RoomKind.SOURCE,
            name="Спальня",
        )
    )
    rename = preset.make(RenameRoom)
    await rename(RenameRoomDTO(user_id=preset.owner.id, room_id=room.id, name="Детская"))
    await rename(RenameRoomDTO(user_id=preset.owner.id, room_id=room.id, name="Детская"))

    [created] = events(log_events, "room_created")
    assert (created["room_name"], created["room_kind"]) == ("Спальня", RoomKind.SOURCE)
    assert created["user_id"] == str(preset.member.id)
    [renamed] = events(log_events, "room_renamed")  # the repeat changed nothing
    assert (renamed["previous_name"], renamed["room_name"]) == ("Спальня", "Детская")


async def test_item_lifecycle_is_logged_by_name(preset: Preset, log_events: LogEvents) -> None:
    item = await preset.make(CreateItem)(
        CreateItemDTO(
            user_id=preset.owner.id,
            project_id=preset.project.id,
            name="Чайник",
            source_room_id=preset.source.id,
            quantity=2,
        )
    )
    await preset.make(UpdateItem)(
        UpdateItemDTO(user_id=preset.owner.id, item_id=item.id, name="Термос")
    )
    await preset.make(DeleteItem)(DeleteItemDTO(user_id=preset.member.id, item_id=item.id))

    [created] = events(log_events, "item_created")
    assert (created["item_name"], created["quantity"], created["in_box"]) == ("Чайник", 2, False)
    [updated] = events(log_events, "item_updated")
    assert (updated["item_name"], updated["fields"]) == ("Термос", ["name"])
    [deleted] = events(log_events, "item_deleted")
    assert (deleted["item_name"], deleted["user_id"]) == ("Термос", str(preset.member.id))


async def test_bulk_items_are_one_event(preset: Preset, log_events: LogEvents) -> None:
    await preset.make(CreateItems)(
        CreateItemsDTO(
            user_id=preset.owner.id,
            project_id=preset.project.id,
            items=NewItemsDTO(
                source_room_id=preset.source.id,
                entries=(NewItemEntryDTO(name="Чашка"), NewItemEntryDTO(name="Ложка")),
            ),
        )
    )

    [created] = events(log_events, "items_created")
    assert (created["items"], created["item_names"]) == (2, ["Чашка", "Ложка"])


async def test_box_update_and_contents_reminder(preset: Preset, log_events: LogEvents) -> None:
    box = await preset.make(CreateBox)(
        CreateBoxDTO(
            user_id=preset.owner.id,
            project_id=preset.project.id,
            destination_room_id=preset.destination.id,
        )
    )
    await preset.make(UpdateBox)(
        UpdateBoxDTO(user_id=preset.owner.id, box_id=box.id, is_fragile=True)
    )
    await preset.make(ScheduleContentsReminder)(
        ScheduleContentsReminderDTO(
            user_id=preset.owner.id, box_id=box.id, remind_at=utc_now() + timedelta(hours=1)
        )
    )
    cancel = preset.make(CancelContentsReminder)
    await cancel(CancelContentsReminderDTO(user_id=preset.owner.id, box_id=box.id))
    await cancel(CancelContentsReminderDTO(user_id=preset.owner.id, box_id=box.id))

    [updated] = events(log_events, "box_updated")
    assert (updated["number"], updated["fields"]) == (box.number, ["is_fragile"])
    [scheduled] = events(log_events, "contents_reminder_scheduled")
    assert scheduled["box_id"] == str(box.id)
    assert len(events(log_events, "contents_reminder_cancelled")) == 1  # nothing left to cancel
