from abc import ABC, abstractmethod

from app.domain.common.ids import ProjectId, RoomId
from app.domain.inventory.room import Room
from app.domain.inventory.value_objects import RoomKind


class AbstractRoomRepository(ABC):
    """Collection-like access to rooms. Soft-deleted rooms are hidden from every
    lookup. Implementations must not commit."""

    @abstractmethod
    async def add(self, room: Room) -> None:
        """Stage a new room.

        Uniqueness of active names per project and kind is enforced even against
        concurrent transactions.

        Raises:
            RoomNameTakenError: an active room of the project has this kind and name.
        """

    @abstractmethod
    async def get(self, room_id: RoomId) -> Room | None:
        """The active room with this id, or None."""

    @abstractmethod
    async def list(self, project_id: ProjectId, kind: RoomKind | None = None) -> list[Room]:
        """Active rooms of the project, of one kind or both, by name."""

    @abstractmethod
    async def update(self, room: Room) -> None:
        """Stage changes of an existing room (also a soft delete).

        Raises:
            RoomNotFoundError: there is no room with this id.
            RoomNameTakenError: an active room of the project has this kind and name.
        """

    @abstractmethod
    async def is_referenced(self, room_id: RoomId) -> bool:
        """Whether an active box goes to the room or an active item comes from it."""
