from abc import ABC, abstractmethod
from collections.abc import Sequence

from app.domain.tasks.value_objects import TaskTemplate


class AbstractTaskTemplateCatalog(ABC):
    """The ready-made tasks users pick from (docs/db-design.md, decision 10).

    The catalog is static: it ships with the release and never changes at runtime.
    """

    @abstractmethod
    def all(self) -> Sequence[TaskTemplate]:
        """Every template, in the order of the move: by phase, then as listed."""
