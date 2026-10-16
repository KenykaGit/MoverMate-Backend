from collections.abc import Hashable
from dataclasses import dataclass


@dataclass(eq=False, kw_only=True, slots=True)
class Entity[IdT: Hashable]:
    """An object defined by its identity, not by its attributes.

    Two entities are equal when they are of the same type and have the same
    id, whatever their other attributes are.
    """

    id: IdT

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Entity) or type(other) is not type(self):
            return NotImplemented
        return bool(self.id == other.id)

    def __hash__(self) -> int:
        return hash((type(self), self.id))
