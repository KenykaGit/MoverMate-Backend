"""The read side of the inventory: screens join several aggregates at once.

Repositories load and save aggregates; the screens need a box with its room,
creator and contents, an item with its room and box, ranked search... Built from
aggregates this is one query per object. This port answers with ready views
instead, in a few queries per page. It never changes anything.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from app.application.common.pagination import CursorPage, CursorPageRequest
from app.application.inventory.dto import (
    BoxDTO,
    BoxSuggestionDTO,
    ItemDTO,
    ItemSearchDTO,
    QrBoxDTO,
)
from app.domain.common.ids import BoxId, ItemId, ProjectId, RoomId, UserId
from app.domain.inventory.box import BoxStatus
from app.domain.inventory.item import ItemState
from app.domain.inventory.value_objects import Priority


class AbstractInventoryReadModel(ABC):
    """Views of active rooms, boxes and items. Access is checked by the use cases."""

    @abstractmethod
    async def box(self, box_id: BoxId, *, viewer_id: UserId) -> BoxDTO | None:
        """The active box; its contents reminder is shown to `viewer_id` only if they
        created the box."""

    @abstractmethod
    async def boxes(
        self,
        project_id: ProjectId,
        *,
        viewer_id: UserId,
        statuses: Sequence[BoxStatus],
        destination_room_id: RoomId | None,
        number: int | None,
        page: CursorPageRequest,
    ) -> CursorPage[BoxDTO]:
        """Active boxes by number; empty `statuses` means any status.

        Raises:
            InvalidPaginationError: the cursor is malformed.
        """

    @abstractmethod
    async def qr_box(self, box_id: BoxId) -> QrBoxDTO | None:
        """The card of an active box with the items in it."""

    @abstractmethod
    async def items(
        self,
        project_id: ProjectId,
        *,
        states: Sequence[ItemState],
        priority: Priority | None,
        box_id: BoxId | None,
        source_room_id: RoomId | None,
        page: CursorPageRequest,
    ) -> CursorPage[ItemDTO]:
        """Active items in packing order (priority, lower(name), id).

        Raises:
            InvalidPaginationError: the cursor is malformed.
        """

    @abstractmethod
    async def items_by_ids(self, item_ids: Sequence[ItemId]) -> list[ItemDTO]:
        """Active items with these ids, in the given order."""

    @abstractmethod
    async def search(
        self, project_id: ProjectId, words: Sequence[str], *, limit: int, offset: int
    ) -> ItemSearchDTO:
        """Active items whose name has every word as a prefix of one of its words,
        best ranked first. `words` are non-empty and consist of word characters only."""

    @abstractmethod
    async def suggestions(
        self, project_id: ProjectId, source_room_id: RoomId, *, exclude_box_id: BoxId | None
    ) -> list[BoxSuggestionDTO]:
        """Active boxes not UNPACKED that hold active items from `source_room_id`,
        most such items first, then by number."""
