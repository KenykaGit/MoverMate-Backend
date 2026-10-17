from dataclasses import dataclass
from typing import Self

from app.domain.common.entity import Entity
from app.domain.common.ids import ProjectId, RoomId, new_id
from app.domain.inventory.errors import CrossProjectReferenceError, WrongRoomKindError
from app.domain.inventory.value_objects import RoomKind, RoomName


@dataclass(eq=False, kw_only=True, slots=True)
class Room(Entity[RoomId]):
    """A room of the old home (SOURCE) or of the new one (DESTINATION).

    Kinds never change: boxes and items rely on the kind of the room they refer to.
    """

    project_id: ProjectId
    kind: RoomKind
    name: RoomName
    is_active: bool

    @classmethod
    def create(cls, *, project_id: ProjectId, kind: RoomKind, name: RoomName) -> Self:
        return cls(id=RoomId(new_id()), project_id=project_id, kind=kind, name=name, is_active=True)

    def rename(self, name: RoomName) -> None:
        self.name = name

    def soft_delete(self) -> None:
        """Idempotent. Whether the room is still referenced is checked by the use case."""
        self.is_active = False

    def ensure_usable_as(self, kind: RoomKind, *, project_id: ProjectId) -> None:
        """Raise unless an object of `project_id` may refer to this room as a `kind` room."""
        if self.project_id != project_id:
            raise CrossProjectReferenceError("The room belongs to another project")
        if self.kind is not kind:
            raise WrongRoomKindError(
                f"Expected a {kind.lower()} room, got a {self.kind.lower()} one"
            )
