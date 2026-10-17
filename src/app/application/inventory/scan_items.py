import logging
from collections.abc import Iterable

from app.application.common.membership import project_of_member
from app.application.common.uow import AbstractUnitOfWork
from app.application.exceptions import (
    InvalidPhotoError,
    PhotoTooLargeError,
    UnsupportedPhotoTypeError,
)
from app.application.interfaces.item_recognizer import (
    AbstractItemRecognizer,
    Photo,
    RecognizedItem,
)
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.inventory.dto import ScanItemsDTO, ScannedItemDTO
from app.domain.inventory.errors import InvalidItemNameError
from app.domain.inventory.value_objects import QUANTITY_MAX, ItemName

logger = logging.getLogger(__name__)

# Vision models take a few megabytes per image at most (Anthropic: 5 MB); a phone
# photo downscaled to ~1600 px on the longest side is well below that and loses
# nothing the model would see: larger images are downscaled by the provider anyway.
MAX_PHOTO_BYTES = 5 * 1024 * 1024
PHOTO_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})
# A photo of a shelf, not a warehouse; also the size of one createItems batch at most.
MAX_SCANNED_ITEMS = 50


class ScanItems:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        recognizer: AbstractItemRecognizer,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._projects = projects
        self._recognizer = recognizer
        self._uow = uow

    async def __call__(self, data: ScanItemsDTO) -> list[ScannedItemDTO]:
        """Names of the items in a photo, ready for createItems. Nothing is saved.

        Raises:
            InvalidPhotoError, PhotoTooLargeError, UnsupportedPhotoTypeError: a bad photo.
            ProjectNotFoundError: no such active project, or the caller is not a member.
            ItemRecognitionUnavailableError: the model did not answer; retry later.
        """
        check_photo(data.photo)
        project = await project_of_member(self._projects, data.project_id, data.user_id)
        # The model answers in seconds: give the database connection back to the pool
        # instead of holding it idle in a transaction for the whole call.
        await self._uow.rollback()

        recognized = await self._recognizer.recognize(data.photo)
        items = normalized(recognized)
        logger.info(
            "items_scanned",
            extra={
                "project_id": str(project.id),
                "recognized": len(recognized),
                "items": len(items),
            },
        )
        return items


def check_photo(photo: Photo) -> None:
    if photo.media_type not in PHOTO_MEDIA_TYPES:
        raise UnsupportedPhotoTypeError(photo.media_type, PHOTO_MEDIA_TYPES)
    if not photo.content:
        raise InvalidPhotoError("The photo is empty")
    if len(photo.content) > MAX_PHOTO_BYTES:
        raise PhotoTooLargeError(MAX_PHOTO_BYTES)


def normalized(recognized: Iterable[RecognizedItem]) -> list[ScannedItemDTO]:
    """What the model said, made acceptable for createItems: names pass ItemName
    (bad ones are dropped, not reported: the user sees the list anyway), quantities
    are clamped to the allowed range and repeated names merged, in the model's order."""
    merged: dict[str, ScannedItemDTO] = {}
    for item in recognized:
        try:
            name = ItemName(item.name)
        except InvalidItemNameError:
            continue
        quantity = min(max(item.quantity, 1), QUANTITY_MAX)
        key = name.value.casefold()
        if key in merged:
            quantity = min(merged[key].quantity + quantity, QUANTITY_MAX)
            merged[key] = ScannedItemDTO(name=merged[key].name, quantity=quantity)
        elif len(merged) < MAX_SCANNED_ITEMS:
            merged[key] = ScannedItemDTO(name=name.value, quantity=quantity)
    return list(merged.values())
