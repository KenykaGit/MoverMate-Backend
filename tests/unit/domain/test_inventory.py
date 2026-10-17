from collections.abc import Callable
from datetime import timedelta
from functools import partial
from itertools import product

import pytest

from app.domain.common.ids import ProjectId, UserId, new_id
from app.domain.inventory.box import BOX_TRANSITIONS, Box, BoxStatus
from app.domain.inventory.errors import (
    BoxStatusTransitionError,
    CrossProjectReferenceError,
    InvalidBoxNumberError,
    InvalidBoxTitleError,
    InvalidItemNameError,
    InvalidQrTokenError,
    InvalidQuantityError,
    InvalidRoomNameError,
    ItemStateTransitionError,
    WrongRoomKindError,
)
from app.domain.inventory.item import ITEM_TRANSITIONS, Item, ItemState
from app.domain.inventory.progress import is_source_room_loaded
from app.domain.inventory.value_objects import (
    BOX_TITLE_MAX_LENGTH,
    ITEM_NAME_MAX_LENGTH,
    QUANTITY_MAX,
    ROOM_NAME_MAX_LENGTH,
    BoxNumber,
    BoxTitle,
    ItemName,
    Priority,
    QrToken,
    Quantity,
    RoomKind,
    RoomName,
)
from tests.checks import Rejection, not_rejected
from tests.factories import NOW, make_box, make_item, make_room

S = BoxStatus
I = ItemState  # noqa: E741


class TestValueObjects:
    def test_reject_values_out_of_their_rules(self) -> None:
        cases: list[Rejection] = [
            (RoomName, "", InvalidRoomNameError),
            (RoomName, "a" * (ROOM_NAME_MAX_LENGTH + 1), InvalidRoomNameError),
            (ItemName, "", InvalidItemNameError),
            (ItemName, "a" * (ITEM_NAME_MAX_LENGTH + 1), InvalidItemNameError),
            (BoxTitle, " ", InvalidBoxTitleError),
            (BoxTitle, "a" * (BOX_TITLE_MAX_LENGTH + 1), InvalidBoxTitleError),
            (BoxNumber, 0, InvalidBoxNumberError),
            (BoxNumber, -1, InvalidBoxNumberError),
            (Quantity, 0, InvalidQuantityError),
            (Quantity, QUANTITY_MAX + 1, InvalidQuantityError),
            (QrToken, "", InvalidQrTokenError),
            (QrToken, "short", InvalidQrTokenError),
            (QrToken, "a" * 21 + "!", InvalidQrTokenError),
            (QrToken, "a" * 23, InvalidQrTokenError),
        ]

        assert not_rejected(cases) == []

    def test_generated_qr_tokens_are_valid_and_unique(self) -> None:
        tokens = {QrToken.generate() for _ in range(100)}

        assert len(tokens) == 100
        assert all(QrToken(token.value) == token for token in tokens)

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
        assert box.title is None
        assert not box.is_fragile

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

    def test_change_follows_the_matrix(self) -> None:
        """Every (from, to) pair: allowed moves change the status, the same status
        is a no-op, anything else is rejected and changes nothing."""
        later = NOW + timedelta(hours=1)
        wrong = []
        for source, target in product(S, S):
            box = make_box()
            box.status = source
            try:
                change = box.change_status(target, now=later)
            except BoxStatusTransitionError:
                outcome = "rejected" if box.status is source else "rejected but changed"
            else:
                moved = change is not None and (change.from_status, change.to_status) == (
                    source,
                    target,
                )
                outcome = (
                    "no-op"
                    if change is None and box.status_changed_at == NOW
                    else "moved"
                    if moved and box.status is target and box.status_changed_at == later
                    else "broken"
                )
                if change is not None and change.is_rollback != (target.position < source.position):
                    outcome = "wrong rollback flag"
            expected = (
                "no-op"
                if source is target
                else "moved"
                if (source, target) in BOX_TRANSITIONS
                else "rejected"
            )
            if outcome != expected:
                wrong.append((source, target, outcome))

        assert wrong == []


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

    def test_moves_follow_the_matrix(self) -> None:
        """Every (from, to) pair of different states; into a box means into another box,
        which the matrix allows from anywhere."""
        wrong = []
        for source, target in product(I, I):
            if source is target:
                continue
            item = _item_in(source)
            box = make_box(project_id=item.project_id, number=9)
            moves: dict[I, Callable[[], object]] = {
                I.IN_BOX: partial(item.put_into, box),
                I.TO_PACK: item.return_to_room,
                I.TAKEN_OUT: item.take_out,
                I.LOST: item.mark_lost,
            }
            try:
                moves[target]()
            except ItemStateTransitionError:
                outcome = "rejected" if item.state is source else "rejected but changed"
            else:
                expected_box = box.id if target is I.IN_BOX else None
                ok = (item.state, item.box_id) == (target, expected_box)
                outcome = "moved" if ok else "broken"
            expected = "moved" if (source, target) in ITEM_TRANSITIONS else "rejected"
            if outcome != expected:
                wrong.append((source, target, outcome))

        assert wrong == []


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


class TestLoadingProgress:
    @pytest.mark.parametrize(
        ("status", "loaded"),
        [
            (S.PACKING, False),
            (S.PACKED, False),
            (S.LOADED, True),
            (S.DELIVERED, True),
            (S.UNPACKED, True),
        ],
    )
    def test_box_has_been_loaded_from_loaded_on(self, status: BoxStatus, loaded: bool) -> None:
        assert status.has_been_loaded is loaded

    @pytest.mark.parametrize(
        ("items_to_pack", "statuses", "loaded"),
        [
            (0, [], True),  # nothing of the room is left behind
            (1, [], False),
            (0, [S.LOADED, S.DELIVERED, S.UNPACKED], True),
            (0, [S.LOADED, S.PACKED], False),
            (3, [S.LOADED], False),
        ],
    )
    def test_source_room_is_loaded_when_nothing_is_left_behind(
        self, items_to_pack: int, statuses: list[BoxStatus], loaded: bool
    ) -> None:
        assert is_source_room_loaded(items_to_pack=items_to_pack, box_statuses=statuses) is loaded
