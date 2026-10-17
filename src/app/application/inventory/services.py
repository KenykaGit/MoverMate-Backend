"""Building new items: shared by the use cases that create them. Never commits."""

from collections.abc import Sequence

from app.application.inventory.dto import NewItemEntryDTO
from app.domain.common.ids import ProjectId
from app.domain.inventory.box import Box
from app.domain.inventory.item import Item
from app.domain.inventory.room import Room
from app.domain.inventory.value_objects import ItemName, Priority, Quantity


def build_items(
    *,
    project_id: ProjectId,
    source: Room,
    entries: Sequence[NewItemEntryDTO],
    priority: Priority,
    box: Box | None,
) -> list[Item]:
    """New items from `source`, put into `box` when given. Every entry is validated
    before anything is returned, so a bad one leaves nothing half-built.

    Raises:
        InvalidItemNameError, InvalidQuantityError: a bad entry.
        WrongRoomKindError, CrossProjectReferenceError: the room or the box do not fit.
    """
    values = [(ItemName(entry.name), Quantity(entry.quantity)) for entry in entries]
    items = [
        Item.create(
            project_id=project_id, source=source, name=name, quantity=quantity, priority=priority
        )
        for name, quantity in values
    ]
    if box is not None:
        for item in items:
            item.put_into(box)
    return items
