from uuid import UUID

import pytest

from app.application.common.patch import UNSET
from app.application.exceptions import (
    BoxNotFoundError,
    InvalidPaginationError,
    ItemBoxMismatchError,
    ItemNotFoundError,
)
from app.application.inventory.change_item_state import ChangeItemState
from app.application.inventory.create_box import CreateBox
from app.application.inventory.create_item import CreateItem
from app.application.inventory.create_items import CreateItems
from app.application.inventory.delete_item import DeleteItem
from app.application.inventory.dto import (
    ChangeItemStateDTO,
    CreateBoxDTO,
    CreateItemDTO,
    CreateItemsDTO,
    DeleteItemDTO,
    ItemDTO,
    NewItemEntryDTO,
    NewItemsDTO,
    SearchItemsDTO,
    SuggestBoxesDTO,
    UpdateItemDTO,
)
from app.application.inventory.search_items import SearchItems
from app.application.inventory.suggest_boxes import SuggestBoxes
from app.application.inventory.update_item import UpdateItem
from app.domain.audit.entity import AuditAction
from app.domain.common.ids import BoxId, ItemId
from app.domain.inventory.errors import InvalidItemNameError, ItemStateTransitionError
from app.domain.inventory.item import ItemState
from app.domain.inventory.value_objects import Priority
from tests.unit.application.inventory.conftest import Preset, other_project


async def create_item(
    preset: Preset, *, box_id: UUID | None = None, name: str = "Лампа"
) -> ItemDTO:
    return await preset.make(CreateItem)(
        CreateItemDTO(
            user_id=preset.owner.id,
            project_id=preset.project.id,
            name=name,
            source_room_id=preset.source.id,
            box_id=box_id,
        )
    )


async def new_box_id(preset: Preset) -> UUID:
    box = await preset.make(CreateBox)(
        CreateBoxDTO(
            user_id=preset.owner.id,
            project_id=preset.project.id,
            destination_room_id=preset.destination.id,
        )
    )
    return box.id


async def change_state(
    preset: Preset, item_id: UUID, state: ItemState, box_id: UUID | None = None
) -> ItemDTO:
    return await preset.make(ChangeItemState)(
        ChangeItemStateDTO(user_id=preset.member.id, item_id=item_id, state=state, box_id=box_id)
    )


class TestCreate:
    async def test_into_its_source_room(self, preset: Preset) -> None:
        item = await create_item(preset)

        assert (item.state, item.box, item.priority, item.quantity) == (
            ItemState.TO_PACK,
            None,
            Priority.MEDIUM,
            1,
        )
        assert item.source_room.id == preset.source.id
        assert preset.uow.commits == 1

    async def test_straight_into_a_box(self, preset: Preset) -> None:
        box_id = await new_box_id(preset)

        item = await create_item(preset, box_id=box_id)

        assert item.state is ItemState.IN_BOX
        assert item.box is not None
        assert item.box.id == box_id

    async def test_a_box_of_another_project_is_not_found(self, preset: Preset) -> None:
        box_id = await new_box_id(preset)
        preset.boxes.boxes[BoxId(box_id)].project_id = other_project(preset).id

        with pytest.raises(BoxNotFoundError):
            await create_item(preset, box_id=box_id)

    async def test_a_batch_is_all_or_nothing(self, preset: Preset) -> None:
        create = preset.make(CreateItems)
        entries = (NewItemEntryDTO(name="Книга"), NewItemEntryDTO(name="  "))

        with pytest.raises(InvalidItemNameError):
            await create(
                CreateItemsDTO(
                    user_id=preset.owner.id,
                    project_id=preset.project.id,
                    items=NewItemsDTO(source_room_id=preset.source.id, entries=entries),
                )
            )

        assert preset.items.items == {}
        assert preset.uow.commits == 0

    async def test_a_batch_keeps_the_given_order(self, preset: Preset) -> None:
        names = ["Чашка", "Блюдо", "Ваза"]

        created = await preset.make(CreateItems)(
            CreateItemsDTO(
                user_id=preset.owner.id,
                project_id=preset.project.id,
                items=NewItemsDTO(
                    source_room_id=preset.source.id,
                    priority=Priority.LOW,
                    entries=tuple(NewItemEntryDTO(name=name) for name in names),
                ),
            )
        )

        assert [item.name for item in created] == names
        assert {item.priority for item in created} == {Priority.LOW}


class TestChangeState:
    async def test_box_id_goes_with_in_box_only(self, preset: Preset) -> None:
        item = await create_item(preset)
        box_id = await new_box_id(preset)

        cases = [(ItemState.IN_BOX, None), (ItemState.LOST, box_id), (ItemState.TO_PACK, box_id)]
        for state, box in cases:
            with pytest.raises(ItemBoxMismatchError):
                await change_state(preset, item.id, state, box)

    async def test_lose_and_find_are_recorded(self, preset: Preset) -> None:
        item = await create_item(preset)
        box_id = await new_box_id(preset)

        lost = await change_state(preset, item.id, ItemState.LOST)
        found = await change_state(preset, item.id, ItemState.IN_BOX, box_id)

        assert lost.state is ItemState.LOST
        assert found.box is not None
        assert found.box.id == box_id
        assert preset.audit_log.actions()[-2:] == [
            AuditAction.ITEM_MARKED_LOST,
            AuditAction.ITEM_RETURNED,
        ]

    async def test_packing_is_not_recorded(self, preset: Preset) -> None:
        item = await create_item(preset)
        box_id = await new_box_id(preset)

        await change_state(preset, item.id, ItemState.IN_BOX, box_id)
        await change_state(preset, item.id, ItemState.TAKEN_OUT)

        assert preset.audit_log.actions() == [AuditAction.BOX_CREATED]

    async def test_the_current_state_again_changes_nothing(self, preset: Preset) -> None:
        item = await create_item(preset)
        await change_state(preset, item.id, ItemState.LOST)

        again = await change_state(preset, item.id, ItemState.LOST)

        assert again.state is ItemState.LOST
        assert preset.audit_log.actions().count(AuditAction.ITEM_MARKED_LOST) == 1

    async def test_moves_outside_the_matrix_are_rejected(self, preset: Preset) -> None:
        item = await create_item(preset)

        with pytest.raises(ItemStateTransitionError):
            await change_state(preset, item.id, ItemState.TAKEN_OUT)
        assert preset.items.items[ItemId(item.id)].state is ItemState.TO_PACK


class TestUpdateAndDelete:
    async def test_update_changes_only_what_was_sent(self, preset: Preset) -> None:
        item = await create_item(preset, name="Чайник")

        updated = await preset.make(UpdateItem)(
            UpdateItemDTO(
                user_id=preset.owner.id, item_id=item.id, quantity=3, priority=Priority.HIGH
            )
        )

        assert (updated.name, updated.quantity, updated.priority) == ("Чайник", 3, Priority.HIGH)

    async def test_unset_fields_are_not_validated(self, preset: Preset) -> None:
        item = await create_item(preset)

        same = await preset.make(UpdateItem)(
            UpdateItemDTO(user_id=preset.owner.id, item_id=item.id, name=UNSET)
        )

        assert same.name == item.name

    async def test_delete_hides_the_item(self, preset: Preset) -> None:
        item = await create_item(preset)

        await preset.make(DeleteItem)(DeleteItemDTO(user_id=preset.member.id, item_id=item.id))

        with pytest.raises(ItemNotFoundError):
            await preset.make(DeleteItem)(DeleteItemDTO(user_id=preset.member.id, item_id=item.id))


class TestSearch:
    async def test_splits_the_query_into_words(self, preset: Preset) -> None:
        await preset.make(SearchItems)(
            SearchItemsDTO(
                user_id=preset.owner.id,
                project_id=preset.project.id,
                query="Зарядка  (телефон)!",
                limit=10,
            )
        )

        assert preset.read_model.searches == [("зарядка", "телефон")]

    async def test_a_query_without_words_finds_nothing(self, preset: Preset) -> None:
        found = await preset.make(SearchItems)(
            SearchItemsDTO(
                user_id=preset.owner.id, project_id=preset.project.id, query=" & | ", limit=10
            )
        )

        assert (found.items, found.has_more) == ((), False)
        assert preset.read_model.searches == []

    async def test_limit_is_checked(self, preset: Preset) -> None:
        with pytest.raises(InvalidPaginationError):
            await preset.make(SearchItems)(
                SearchItemsDTO(
                    user_id=preset.owner.id, project_id=preset.project.id, query="x", limit=0
                )
            )


async def test_suggestions_are_for_members_only(preset: Preset) -> None:
    item = await create_item(preset)

    with pytest.raises(ItemNotFoundError):
        await preset.make(SuggestBoxes)(
            SuggestBoxesDTO(user_id=preset.stranger.id, item_id=item.id)
        )
