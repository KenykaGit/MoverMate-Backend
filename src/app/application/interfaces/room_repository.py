from abc import ABC, abstractmethod

from app.domain.inventory.room import Room


class AbstractRoomRepository(ABC):
    """Collection-like access to rooms. Implementations must not commit."""

    @abstractmethod
    async def add(self, room: Room) -> None:
        """Stage a new room.

        Uniqueness of active names per project and kind is enforced even against
        concurrent transactions.

        Raises:
            RoomNameTakenError: an active room of the project has this kind and name.
        """
