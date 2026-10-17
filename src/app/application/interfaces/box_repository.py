from abc import ABC, abstractmethod

from app.domain.common.ids import BoxId, ProjectId
from app.domain.inventory.box import Box, BoxStatus
from app.domain.inventory.value_objects import BoxNumber, QrToken


class AbstractBoxRepository(ABC):
    """Access to persisted boxes. Soft-deleted boxes are hidden from every lookup.

    The status is never written by `update`: only `change_status` changes it,
    atomically, so a concurrent change is detected instead of overwritten.
    Implementations must not commit: the unit of work owns the transaction.
    """

    @abstractmethod
    async def add(self, box: Box) -> None:
        """Stage a new box (its number comes from `next_free_number`)."""

    @abstractmethod
    async def get(self, box_id: BoxId) -> Box | None:
        """The active box with this id, or None."""

    @abstractmethod
    async def get_by_qr_token(self, qr_token: QrToken) -> Box | None:
        """The active box with this QR token, or None."""

    @abstractmethod
    async def next_free_number(self, project_id: ProjectId) -> BoxNumber:
        """The lowest positive number no active box of the project has.

        Call it under `AbstractProjectRepository.lock_for_box_numbering`.
        """

    @abstractmethod
    async def update(self, box: Box) -> None:
        """Stage the title, fragility, destination and activity of a box (not its status).

        Raises:
            BoxNotFoundError: there is no box with this id.
        """

    @abstractmethod
    async def change_status(self, box: Box, *, expected: BoxStatus) -> None:
        """Stage `box.status` if the stored status is still `expected`.

        Raises:
            BoxStatusConflictError: the status changed meanwhile, or the box is gone.
        """
