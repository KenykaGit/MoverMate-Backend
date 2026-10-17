from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True, kw_only=True)
class Photo:
    """An image as the client sent it: the bytes and their media type."""

    content: bytes = field(repr=False)
    media_type: str


@dataclass(frozen=True, slots=True)
class RecognizedItem:
    """What the model said, unchecked: the use case normalizes and filters it."""

    name: str
    quantity: int = 1


class AbstractItemRecognizer(ABC):
    @abstractmethod
    async def recognize(self, photo: Photo) -> list[RecognizedItem]:
        """The items visible in the photo, as named by a vision model.

        Raises:
            ItemRecognitionUnavailableError: the model could not be asked or gave an
                unreadable answer (ItemRecognitionDisabledError: not configured).
        """
