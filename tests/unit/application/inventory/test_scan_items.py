from uuid import UUID

import pytest

from app.application.exceptions import (
    InvalidPhotoError,
    ItemRecognitionUnavailableError,
    PhotoTooLargeError,
    ProjectNotFoundError,
    UnsupportedPhotoTypeError,
)
from app.application.interfaces.item_recognizer import Photo, RecognizedItem
from app.application.inventory.dto import ScanItemsDTO, ScannedItemDTO
from app.application.inventory.scan_items import (
    MAX_PHOTO_BYTES,
    MAX_SCANNED_ITEMS,
    ScanItems,
)
from app.domain.common.ids import new_id
from app.domain.inventory.value_objects import ITEM_NAME_MAX_LENGTH, QUANTITY_MAX
from tests.conftest import LogEvents
from tests.fakes import FakeItemRecognizer, FakeUnitOfWork
from tests.unit.application.inventory.conftest import Preset

JPEG = Photo(content=b"\xff\xd8\xff\xe0 a photo", media_type="image/jpeg")


async def scan(
    preset: Preset,
    *,
    photo: Photo = JPEG,
    user_id: UUID | None = None,
    project_id: UUID | None = None,
) -> list[ScannedItemDTO]:
    return await preset.make(ScanItems)(
        ScanItemsDTO(
            user_id=preset.member.id if user_id is None else user_id,
            project_id=preset.project.id if project_id is None else project_id,
            photo=photo,
        )
    )


async def test_a_member_gets_the_recognized_items_and_nothing_is_saved(preset: Preset) -> None:
    preset.recognizer.items = [RecognizedItem("Настольная лампа"), RecognizedItem("Книга", 3)]

    items = await scan(preset)

    assert items == [
        ScannedItemDTO(name="Настольная лампа", quantity=1),
        ScannedItemDTO(name="Книга", quantity=3),
    ]
    assert preset.recognizer.photos == [JPEG]
    assert preset.items.items == {}
    assert preset.uow.commits == 0


async def test_the_database_connection_is_released_before_the_model_is_asked(
    preset: Preset,
) -> None:
    """The model answers in seconds; an idle transaction would hold a pool slot."""

    class RecordingRecognizer(FakeItemRecognizer):
        def __init__(self, uow: FakeUnitOfWork) -> None:
            super().__init__()
            self.rollbacks_before_call: int | None = None
            self._uow = uow

        async def recognize(self, photo: Photo) -> list[RecognizedItem]:
            self.rollbacks_before_call = self._uow.rollbacks
            return await super().recognize(photo)

    preset.recognizer = RecordingRecognizer(preset.uow)

    await scan(preset)

    assert preset.recognizer.rollbacks_before_call == 1


async def test_an_empty_answer_is_an_empty_list(preset: Preset) -> None:
    assert await scan(preset) == []


async def test_answers_are_made_acceptable_for_create_items(preset: Preset) -> None:
    """Names pass ItemName or are dropped, quantities are clamped, repeats merged."""
    preset.recognizer.items = [
        RecognizedItem("  Кружка  ", 2),
        RecognizedItem("   "),
        RecognizedItem("x" * (ITEM_NAME_MAX_LENGTH + 1)),
        RecognizedItem("кружка", 3),
        RecognizedItem("Стул", 0),
        RecognizedItem("Гвоздь", QUANTITY_MAX + 1),
        RecognizedItem("гвоздь", 5),
    ]

    items = await scan(preset)

    assert items == [
        ScannedItemDTO(name="Кружка", quantity=5),
        ScannedItemDTO(name="Стул", quantity=1),
        ScannedItemDTO(name="Гвоздь", quantity=QUANTITY_MAX),
    ]


async def test_at_most_max_scanned_items_are_returned(preset: Preset) -> None:
    preset.recognizer.items = [RecognizedItem(f"Вещь {n}") for n in range(MAX_SCANNED_ITEMS + 5)]

    items = await scan(preset)

    assert len(items) == MAX_SCANNED_ITEMS
    assert items[-1].name == f"Вещь {MAX_SCANNED_ITEMS - 1}"


@pytest.mark.parametrize(
    ("photo", "error"),
    [
        (Photo(content=b"", media_type="image/png"), InvalidPhotoError),
        (Photo(content=b"x" * (MAX_PHOTO_BYTES + 1), media_type="image/png"), PhotoTooLargeError),
        (Photo(content=b"GIF89a", media_type="image/heic"), UnsupportedPhotoTypeError),
        (Photo(content=b"text", media_type=""), UnsupportedPhotoTypeError),
    ],
)
async def test_a_bad_photo_never_reaches_the_model(
    preset: Preset, photo: Photo, error: type[Exception]
) -> None:
    with pytest.raises(error):
        await scan(preset, photo=photo)

    assert preset.recognizer.photos == []


async def test_a_photo_of_exactly_the_limit_is_accepted(preset: Preset) -> None:
    await scan(preset, photo=Photo(content=b"x" * MAX_PHOTO_BYTES, media_type="image/webp"))

    assert len(preset.recognizer.photos) == 1


@pytest.mark.parametrize("who", ["stranger", "missing project"])
async def test_strangers_do_not_learn_the_project_exists(preset: Preset, who: str) -> None:
    user_id: UUID = preset.stranger.id if who == "stranger" else preset.owner.id
    project_id: UUID = preset.project.id if who == "stranger" else new_id()

    with pytest.raises(ProjectNotFoundError):
        await scan(preset, user_id=user_id, project_id=project_id)

    assert preset.recognizer.photos == []


async def test_a_failing_model_is_reported_as_unavailable(preset: Preset) -> None:
    preset.recognizer.error = ItemRecognitionUnavailableError()

    with pytest.raises(ItemRecognitionUnavailableError):
        await scan(preset)


async def test_a_scan_is_logged_with_counts(preset: Preset, log_events: LogEvents) -> None:
    preset.recognizer.items = [RecognizedItem("Лампа"), RecognizedItem("лампа")]

    await scan(preset)

    [event] = [e for e in log_events() if e["event"] == "items_scanned"]
    assert event["project_id"] == str(preset.project.id)
    assert (event["recognized"], event["items"]) == (2, 1)
