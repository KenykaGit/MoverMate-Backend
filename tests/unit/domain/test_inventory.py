from datetime import timedelta
from itertools import product

import pytest

from app.domain.common.ids import ProjectId, UserId, new_id
from app.domain.inventory.box import BOX_TRANSITIONS, Box, BoxStatus
from app.domain.inventory.errors import (
    BoxStatusTransitionError,
    CrossProjectReferenceError,
    InvalidBoxNumberError,
    InvalidItemNameError,
    InvalidQrTokenError,
    InvalidQuantityError,
    InvalidRoomNameError,
    ItemStateTransitionError,
    WrongRoomKindError,
)
from app.domain.inventory.item import ITEM_TRANSITIONS, Item, ItemState
from app.domain.inventory.value_objects import (
    ITEM_NAME_MAX_LENGTH,
    QUANTITY_MAX,
    ROOM_NAME_MAX_LENGTH,
    BoxNumber,
    ItemName,
    Priority,
    QrToken,
    Quantity,
    RoomKind,
    RoomName,
)
from tests.factories import NOW, make_box, make_item, make_room

S = BoxStatus
I = ItemState  # noqa: E741


class TestValueObjects:
    @pytest.mark.parametrize("value", ["", "a" * (ROOM_NAME_MAX_LENGTH + 1)])
    def test_room_name_length(self, value: str) -> None:
        with pytest.raises(InvalidRoomNameError):
            RoomName(value)

    @pytest.mark.parametrize("value", ["", "a" * (ITEM_NAME_MAX_LENGTH + 1)])
    def test_item_name_length(self, value: str) -> None:
        with pytest.raises(InvalidItemNameError):
            ItemName(value)

    @pytest.mark.parametrize("value", [0, -1])
    def test_box_number_is_positive(self, value: int) -> None:
        with pytest.raises(InvalidBoxNumberError):
            BoxNumber(value)

    @pytest.mark.parametrize("value", [0, QUANTITY_MAX + 1])
    def test_quantity_range(self, value: int) -> None:
        with pytest.raises(InvalidQuantityError):
            Quantity(value)

    def test_generated_qr_tokens_are_valid_and_unique(self) -> None:
        tokens = {QrToken.generate() for _ in range(100)}

        assert len(tokens) == 100
        assert all(QrToken(token.value) == token for token in tokens)

    @pytest.mark.parametrize("value", ["", "short", "a" * 21 + "!", "a" * 23])
    def test_qr_token_rejects_malformed(self, value: str) -> None:
        with pytest.raises(InvalidQrTokenError):
            QrToken(value)

    def test_priorities_sort_first_necessity_first(self) -> None:
        assert sorted([Priority.LOW, Priority.FIRST_NECESSITY, Priority.MEDIUM]) == [
            Priority.FIRST_NECESSITY,
            Priority.MEDIUM,
            Priority.LOW,
        ]


class TestRoomReferences:
    def test_box_needs_a_destination_room(self) -> None:
        source = make_room(kind=RoomKind.SOURCE)

        with pytest.raises(WrongRoomKindError):
            Box.create(
                project_id=source.project_id,
                number=BoxNumber(1),
                destination=source,
                created_by=UserId(new_id()),
                now=NOW,
            )

    def test_item_needs_a_source_room(self) -> None:
        destination = make_room(kind=RoomKind.DESTINATION)

        with pytest.raises(WrongRoomKindError):
            Item.create(
                project_id=destination.project_id, source=destination, name=ItemName("Kettle")
            )

    def test_rooms_of_another_project_are_rejected(self) -> None:
        box = make_box()
        item = make_item(project_id=box.project_id)

        with pytest.raises(CrossProjectReferenceError):
            box.change_destination(make_room(kind=RoomKind.DESTINATION))
        with pytest.raises(CrossProjectReferenceError):
            item.change_source_room(make_room(kind=RoomKind.SOURCE))

    def test_destination_and_source_can_change_within_the_project(self) -> None:
        box = make_box()
        item = make_item(project_id=box.project_id)
        bedroom = make_room(project_id=box.project_id, kind=RoomKind.DESTINATION)
        hall = make_room(project_id=box.project_id, kind=RoomKind.SOURCE)

        box.change_destination(bedroom)
        item.change_source_room(hall)

        assert box.destination_room_id == bedroom.id
        assert item.source_room_id == hall.id


class TestBoxStatus:
    def test_new_box_is_being_packed(self) -> None:
        box = make_box()

        assert box.status is S.PACKING
        assert box.status_changed_at == NOW
        assert box.is_active

    def test_transitions_are_single_steps_both_ways(self) -> None:
        assert {
            (S.PACKING, S.PACKED),
            (S.PACKED, S.LOADED),
            (S.LOADED, S.DELIVERED),
            (S.DELIVERED, S.UNPACKED),
            (S.PACKED, S.PACKING),
            (S.LOADED, S.PACKED),
            (S.DELIVERED, S.LOADED),
            (S.UNPACKED, S.DELIVERED),
        } == BOX_TRANSITIONS

    @pytest.mark.parametrize(("source", "target"), list(product(S, S)))
    def test_change_follows_the_matrix(self, source: BoxStatus, target: BoxStatus) -> None:
        box = make_box()
        box.status = source
        later = NOW + timedelta(hours=1)

        if source is target:
            assert box.change_status(target, now=later) is None
            assert box.status_changed_at == NOW
        elif (source, target) in BOX_TRANSITIONS:
            change = box.change_status(target, now=later)
            assert change is not None
            assert (change.from_status, change.to_status) == (source, target)
            assert change.is_rollback == (target.position < source.position)
            assert box.status is target
            assert box.status_changed_at == later
        else:
            with pytest.raises(BoxStatusTransitionError):
                box.change_status(target, now=later)
            assert box.status is source


class TestItemState:
    def test_new_item_waits_to_be_packed(self) -> None:
        item = make_item()

        assert item.state is I.TO_PACK
        assert item.box_id is None
        assert item.quantity == Quantity(1)
        assert item.priority is Priority.MEDIUM

    def test_pack_unpack_lose_and_find(self) -> None:
        box = make_box()
        item = make_item(project_id=box.project_id)

        packed = item.put_into(box)
        assert packed is not None
        assert (item.state, item.box_id) == (I.IN_BOX, box.id)

        item.take_out()
        assert (item.state, item.box_id) == (I.TAKEN_OUT, None)

        lost = item.mark_lost()
        assert lost is not None
        assert lost.is_loss
        assert not lost.is_find

        found = item.put_into(box)
        assert found is not None
        assert found.is_find
        assert (item.state, item.box_id) == (I.IN_BOX, box.id)

    def test_moves_between_boxes_of_any_status(self) -> None:
        first = make_box()
        second = make_box(project_id=first.project_id, number=2)
        second.status = S.UNPACKED
        item = make_item(project_id=first.project_id)
        item.put_into(first)

        change = item.put_into(second)

        assert change is not None
        assert (change.from_state, change.to_state) == (I.IN_BOX, I.IN_BOX)
        assert item.box_id == second.id

    def test_repeating_the_current_state_is_a_no_op(self) -> None:
        box = make_box()
        item = make_item(project_id=box.project_id)
        item.put_into(box)

        assert item.put_into(box) is None
        assert item.mark_lost() is not None
        assert item.mark_lost() is None
        assert item.return_to_room() is not None
        assert item.return_to_room() is None

    def test_box_of_another_project_is_rejected(self) -> None:
        item = make_item()

        with pytest.raises(CrossProjectReferenceError):
            item.put_into(make_box())
        assert item.state is I.TO_PACK

    @pytest.mark.parametrize(
        ("source", "target"),
        [(s, t) for s, t in product(I, I) if s is not t and t is not I.IN_BOX],
    )
    def test_moves_outside_boxes_follow_the_matrix(
        self, source: ItemState, target: ItemState
    ) -> None:
        item = _item_in(source)
        moves = {I.TO_PACK: item.return_to_room, I.TAKEN_OUT: item.take_out, I.LOST: item.mark_lost}

        if (source, target) in ITEM_TRANSITIONS:
            moves[target]()
            assert (item.state, item.box_id) == (target, None)
        else:
            with pytest.raises(ItemStateTransitionError):
                moves[target]()
            assert item.state is source

    @pytest.mark.parametrize("source", list(I))
    def test_every_state_may_go_into_a_box(self, source: ItemState) -> None:
        """Into a box (another one if already in a box) is allowed from anywhere."""
        item = _item_in(source)
        box = make_box(project_id=item.project_id, number=9)

        item.put_into(box)

        assert (source, I.IN_BOX) in ITEM_TRANSITIONS
        assert (item.state, item.box_id) == (I.IN_BOX, box.id)


def _item_in(state: ItemState) -> Item:
    item = make_item(project_id=ProjectId(new_id()))
    box = make_box(project_id=item.project_id)
    if state is not I.TO_PACK:
        item.put_into(box)
    if state is I.TAKEN_OUT:
        item.take_out()
    if state is I.LOST:
        item.mark_lost()
    assert item.state is state
    return item
