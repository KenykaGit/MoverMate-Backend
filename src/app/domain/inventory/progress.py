"""Progress rules of the moving day screens (docs/db-design.md, 5.4)."""

from collections.abc import Iterable

from app.domain.inventory.box import BoxStatus


def is_source_room_loaded(*, items_to_pack: int, box_statuses: Iterable[BoxStatus]) -> bool:
    """A room of the old home is loaded when nothing in it waits to be packed and
    every box holding its items has been loaded.

    A room without items is loaded too: nothing of it is left behind.
    """
    return items_to_pack == 0 and all(status.has_been_loaded for status in box_statuses)
