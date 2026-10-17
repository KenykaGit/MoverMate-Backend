"""Reading the model's answer: what vision models actually send despite the prompt."""

import pytest

from app.application.interfaces.item_recognizer import RecognizedItem
from app.infrastructure.llm.recognizer import parse_items


@pytest.mark.parametrize(
    "content",
    [
        '{"items": [{"name": "Лампа", "quantity": 2}]}',
        '```json\n{"items": [{"name": "Лампа", "quantity": 2}]}\n```',
        'Here you go:\n{"items": [{"name": "Лампа", "quantity": 2}]}\nHope it helps!',
        '[{"name": "Лампа", "quantity": 2}]',
        '{"items": [{"name": "Лампа", "quantity": "2"}]}',
        '{"items": [{"name": "Лампа", "quantity": 2.0}]}',
    ],
)
def test_json_is_found_in_what_models_send(content: str) -> None:
    assert parse_items(content) == [RecognizedItem("Лампа", 2)]


def test_an_empty_list_is_no_items() -> None:
    assert parse_items('{"items": []}') == []


def test_unusable_entries_are_skipped_and_odd_quantities_become_one() -> None:
    content = """{"items": [
        {"name": "Стул"},
        {"name": "Ваза", "quantity": true},
        {"name": "Кот", "quantity": "много"},
        {"quantity": 3},
        {"name": 42},
        "Книга",
        null
    ]}"""

    assert parse_items(content) == [
        RecognizedItem("Стул", 1),
        RecognizedItem("Ваза", 1),
        RecognizedItem("Кот", 1),
        RecognizedItem("Книга", 1),
    ]


def test_non_finite_quantities_do_not_crash() -> None:
    assert parse_items('[{"name": "Стул", "quantity": NaN}]') == [RecognizedItem("Стул", 1)]


@pytest.mark.parametrize(
    "content",
    [
        "I cannot see any items in this photo.",
        '{"items": [{"name": "Лампа"',  # cut by max_tokens
        '{"objects": "none"}',
        "",
    ],
)
def test_an_answer_without_items_is_an_error(content: str) -> None:
    with pytest.raises(ValueError, match="answer"):
        parse_items(content)
