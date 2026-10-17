"""HTTP bodies are translated into use case inputs without losing meaning."""

import pytest
from fastapi.exceptions import RequestValidationError

from app.application.common.patch import UNSET
from app.domain.common.ids import new_id
from app.domain.inventory.value_objects import Priority
from app.presentation.http.schemas.inventory import (
    CreateBoxRequest,
    ItemPriority,
    UpdateBoxRequest,
    UpdateItemRequest,
)

USER_ID = new_id()
TARGET_ID = new_id()


def test_priority_is_published_by_name_and_stored_as_a_number() -> None:
    published = {priority: ItemPriority.of(priority) for priority in Priority}

    assert {p: (s.to_domain(), s.value) for p, s in published.items()} == {
        p: (p, p.name) for p in Priority
    }


class TestPartialUpdates:
    def test_absent_fields_stay_unset(self) -> None:
        dto = UpdateBoxRequest.model_validate({}).to_dto(user_id=USER_ID, box_id=TARGET_ID)

        assert (dto.title, dto.is_fragile, dto.destination_room_id) == (UNSET, UNSET, UNSET)

    def test_null_clears_a_nullable_field(self) -> None:
        body = UpdateBoxRequest.model_validate({"title": None, "is_fragile": True})

        dto = body.to_dto(user_id=USER_ID, box_id=TARGET_ID)

        assert (dto.title, dto.is_fragile, dto.destination_room_id) == (None, True, UNSET)

    def test_null_is_rejected_where_a_field_cannot_be_cleared(self) -> None:
        for field in ("is_fragile", "destination_room_id"):
            body = UpdateBoxRequest.model_validate({field: None})

            with pytest.raises(RequestValidationError) as exc_info:
                body.to_dto(user_id=USER_ID, box_id=TARGET_ID)

            assert exc_info.value.errors()[0]["loc"] == ("body", field)

    def test_item_priority_is_translated_only_when_sent(self) -> None:
        sent = UpdateItemRequest.model_validate({"priority": "LOW", "quantity": 3})
        absent = UpdateItemRequest.model_validate({"name": "Kettle"})

        sent_dto = sent.to_dto(user_id=USER_ID, item_id=TARGET_ID)
        absent_dto = absent.to_dto(user_id=USER_ID, item_id=TARGET_ID)

        assert (sent_dto.priority, sent_dto.quantity, sent_dto.name) == (Priority.LOW, 3, UNSET)
        assert (absent_dto.priority, absent_dto.name) == (UNSET, "Kettle")


def test_items_of_a_new_box_carry_their_room_and_priority() -> None:
    room_id = new_id()
    body = CreateBoxRequest.model_validate(
        {
            "destination_room_id": str(TARGET_ID),
            "items": {
                "source_room_id": str(room_id),
                "priority": "FIRST_NECESSITY",
                "entries": [{"name": "Mugs", "quantity": 6}, {"name": "Kettle"}],
            },
        }
    )

    dto = body.to_dto(user_id=USER_ID, project_id=new_id())

    assert dto.items is not None
    assert dto.items.source_room_id == room_id
    assert dto.items.priority is Priority.FIRST_NECESSITY
    assert [(e.name, e.quantity) for e in dto.items.entries] == [("Mugs", 6), ("Kettle", 1)]
    assert (dto.title, dto.is_fragile) == (None, False)
