from abc import ABC, abstractmethod


class AbstractHealthProbe(ABC):
    """Checks that one external dependency answers."""

    @abstractmethod
    async def check(self) -> None:
        """Return if the dependency is usable; raise anything otherwise."""
