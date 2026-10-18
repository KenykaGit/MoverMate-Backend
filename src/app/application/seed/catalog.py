"""The test accounts and the moves they own: plain data for SeedTestUsers.

Every account owns projects, of growing size: test_1 has one small move, test_2 and
test_3 several of different sizes, test_4 a warehouse of 120 boxes and over a thousand
items (to see lists, paging and summaries under load), test_5 a move that is over.
"""

from dataclasses import dataclass

from app.domain.inventory.value_objects import Priority
from app.domain.tasks.value_objects import TaskPhase

TEST_PASSWORD = "Test-pass-123"  # noqa: S105  # a public password of throwaway accounts


@dataclass(frozen=True, slots=True, kw_only=True)
class SeedItem:
    name: str
    quantity: int = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class SeedBox:
    """A box with its items, packed in one go: they all come from `source_room`."""

    source_room: str
    destination_room: str
    items: tuple[SeedItem, ...]
    title: str | None = None
    is_fragile: bool = False
    priority: Priority = Priority.MEDIUM


@dataclass(frozen=True, slots=True, kw_only=True)
class SeedUnpackedItems:
    """Items still waiting to be packed (TO_PACK)."""

    source_room: str
    items: tuple[SeedItem, ...]
    priority: Priority = Priority.MEDIUM


@dataclass(frozen=True, slots=True, kw_only=True)
class SeedTask:
    title: str
    phase: TaskPhase
    days_from_move: int | None = None
    """The due day relative to the moving date; None: no due time."""
    description: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SeedProject:
    name: str
    days_until_move: int
    time_zone: str
    source_rooms: tuple[str, ...]
    destination_rooms: tuple[str, ...]
    origin_address: str | None = None
    destination_address: str | None = None
    boxes: tuple[SeedBox, ...] = ()
    unpacked: tuple[SeedUnpackedItems, ...] = ()
    tasks: tuple[SeedTask, ...] = ()
    is_finished: bool = False
    """The move is over: every box is UNPACKED, every item TAKEN_OUT, every task done.
    Such a project has no `unpacked` items (they never left the old home)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SeedUser:
    username: str
    projects: tuple[SeedProject, ...] = ()


def _items(*entries: str | tuple[str, int]) -> tuple[SeedItem, ...]:
    return tuple(
        SeedItem(name=entry)
        if isinstance(entry, str)
        else SeedItem(name=entry[0], quantity=entry[1])
        for entry in entries
    )


_STUDIO = SeedProject(
    name="Переезд в студию",
    days_until_move=14,
    time_zone="Europe/Moscow",
    origin_address="Москва, ул. Тверская, 12",
    destination_address="Москва, ул. Арбат, 5",
    source_rooms=("Кухня", "Спальня"),
    destination_rooms=("Кухня", "Комната"),
    boxes=(
        SeedBox(
            source_room="Кухня",
            destination_room="Кухня",
            title="Посуда",
            is_fragile=True,
            items=_items(("Тарелки", 6), ("Кружки", 4), "Сковорода"),
        ),
        SeedBox(
            source_room="Спальня",
            destination_room="Комната",
            title="Одежда",
            items=_items(("Футболки", 10), ("Джинсы", 3)),
        ),
    ),
    unpacked=(
        SeedUnpackedItems(source_room="Спальня", items=_items("Настольная лампа", ("Книги", 12))),
    ),
    tasks=(
        SeedTask(title="Заказать грузчиков", phase=TaskPhase.BEFORE, days_from_move=-7),
        SeedTask(title="Вынести мусор и сдать ключи", phase=TaskPhase.DURING, days_from_move=0),
        SeedTask(title="Подключить интернет", phase=TaskPhase.AFTER, days_from_move=2),
    ),
)

_APARTMENT = SeedProject(
    name="Переезд в новую квартиру",
    days_until_move=30,
    time_zone="Europe/Moscow",
    origin_address="Санкт-Петербург, Невский пр., 100",
    destination_address="Москва, ул. Ленинская Слобода, 19",
    source_rooms=("Кухня", "Гостиная", "Спальня"),
    destination_rooms=("Кухня", "Гостиная", "Спальня", "Кладовая"),
    boxes=(
        SeedBox(
            source_room="Кухня",
            destination_room="Кухня",
            title="Посуда",
            is_fragile=True,
            items=_items(("Тарелки", 12), ("Бокалы", 6), "Чайник", "Кофеварка"),
        ),
        SeedBox(
            source_room="Гостиная",
            destination_room="Гостиная",
            title="Книги",
            items=_items(("Художественные книги", 25), ("Справочники", 8)),
        ),
        SeedBox(
            source_room="Спальня",
            destination_room="Спальня",
            title="Одежда",
            items=_items(("Футболки", 15), ("Свитера", 5), ("Куртки", 3)),
        ),
        SeedBox(
            source_room="Гостиная",
            destination_room="Кладовая",
            title="Документы и зарядки",
            priority=Priority.FIRST_NECESSITY,
            items=_items("Папка документов", ("Зарядные устройства", 4), "Удлинитель"),
        ),
    ),
    unpacked=(
        SeedUnpackedItems(
            source_room="Кухня", priority=Priority.LOW, items=_items("Микроволновка", "Тостер")
        ),
        SeedUnpackedItems(
            source_room="Гостиная",
            priority=Priority.HIGH,
            items=_items("Телевизор", "Торшер", ("Подушки", 4)),
        ),
    ),
    tasks=(
        SeedTask(
            title="Выбрать компанию грузчиков",
            description="Сравнить три предложения и заключить договор",
            phase=TaskPhase.BEFORE,
            days_from_move=-14,
        ),
        SeedTask(title="Купить коробки и скотч", phase=TaskPhase.BEFORE, days_from_move=-10),
        SeedTask(title="Оформить переадресацию почты", phase=TaskPhase.BEFORE),
        SeedTask(title="Встретить грузовик", phase=TaskPhase.DURING, days_from_move=0),
        SeedTask(title="Сфотографировать показания счётчиков", phase=TaskPhase.DURING),
        SeedTask(title="Распаковать посуду", phase=TaskPhase.AFTER, days_from_move=1),
    ),
)

_COUNTRY_HOUSE = SeedProject(
    name="Дача на лето",
    days_until_move=60,
    time_zone="Asia/Yekaterinburg",
    source_rooms=("Гараж",),
    destination_rooms=("Веранда",),
    boxes=(
        SeedBox(
            source_room="Гараж",
            destination_room="Веранда",
            title="Садовый инвентарь",
            items=_items("Лопата", "Грабли", ("Перчатки", 3)),
        ),
    ),
    tasks=(
        SeedTask(title="Проверить отопление", phase=TaskPhase.BEFORE, days_from_move=-5),
        SeedTask(title="Закупить рассаду", phase=TaskPhase.AFTER, days_from_move=3),
    ),
)

_FAMILY_HOUSE = SeedProject(
    name="Большой семейный переезд",
    days_until_move=21,
    time_zone="Europe/Moscow",
    origin_address="Казань, ул. Баумана, 40",
    destination_address="Москва, Кутузовский пр-т, 30",
    source_rooms=("Кухня", "Гостиная", "Детская", "Спальня", "Балкон"),
    destination_rooms=("Кухня", "Гостиная", "Детская", "Спальня", "Кабинет"),
    boxes=(
        SeedBox(
            source_room="Кухня",
            destination_room="Кухня",
            title="Сервиз",
            is_fragile=True,
            items=_items(("Тарелки", 24), ("Чашки", 12), "Супница", ("Салатники", 4)),
        ),
        SeedBox(
            source_room="Кухня",
            destination_room="Кухня",
            title="Кастрюли",
            items=_items(("Кастрюли", 5), ("Сковороды", 3), "Мультиварка"),
        ),
        SeedBox(
            source_room="Детская",
            destination_room="Детская",
            title="Игрушки",
            items=_items("Конструктор", ("Плюшевые игрушки", 9), ("Настольные игры", 6)),
        ),
        SeedBox(
            source_room="Детская",
            destination_room="Детская",
            title="Школьные принадлежности",
            priority=Priority.FIRST_NECESSITY,
            items=_items(("Учебники", 14), ("Тетради", 20), "Рюкзак"),
        ),
        SeedBox(
            source_room="Спальня",
            destination_room="Спальня",
            title="Постельное бельё",
            items=_items(("Комплекты белья", 4), ("Одеяла", 2), ("Подушки", 4)),
        ),
        SeedBox(
            source_room="Спальня",
            destination_room="Спальня",
            title="Зимняя одежда",
            priority=Priority.LOW,
            items=_items(("Пуховики", 4), ("Шарфы", 6), ("Шапки", 5)),
        ),
        SeedBox(
            source_room="Гостиная",
            destination_room="Кабинет",
            title="Техника",
            is_fragile=True,
            priority=Priority.HIGH,
            items=_items("Ноутбук", "Монитор", "Принтер", ("Кабели", 10)),
        ),
        SeedBox(
            source_room="Балкон",
            destination_room="Кабинет",
            items=_items("Велосипед", ("Лыжи", 2)),
        ),
    ),
    unpacked=(
        SeedUnpackedItems(
            source_room="Гостиная",
            priority=Priority.HIGH,
            items=_items("Диван", "Журнальный столик", ("Картины", 3)),
        ),
        SeedUnpackedItems(
            source_room="Спальня", priority=Priority.MEDIUM, items=_items("Комод", "Зеркало")
        ),
        SeedUnpackedItems(
            source_room="Балкон",
            priority=Priority.LOW,
            items=_items("Сушилка", ("Цветы в горшках", 5)),
        ),
    ),
    tasks=(
        SeedTask(title="Составить план переезда", phase=TaskPhase.BEFORE, days_from_move=-21),
        SeedTask(title="Найти компанию грузчиков", phase=TaskPhase.BEFORE, days_from_move=-18),
        SeedTask(title="Перевести детей в новую школу", phase=TaskPhase.BEFORE, days_from_move=-14),
        SeedTask(title="Купить упаковочные материалы", phase=TaskPhase.BEFORE, days_from_move=-12),
        SeedTask(title="Отключить газ и электричество", phase=TaskPhase.BEFORE, days_from_move=-1),
        SeedTask(title="Передать ключи", phase=TaskPhase.DURING, days_from_move=0),
        SeedTask(title="Проверить опись коробок", phase=TaskPhase.DURING, days_from_move=0),
        SeedTask(title="Собрать мебель", phase=TaskPhase.AFTER, days_from_move=2),
        SeedTask(title="Оформить регистрацию", phase=TaskPhase.AFTER, days_from_move=7),
    ),
)

_OFFICE = SeedProject(
    name="Переезд офиса",
    days_until_move=45,
    time_zone="Europe/Moscow",
    source_rooms=("Open space", "Переговорная"),
    destination_rooms=("Open space", "Переговорная"),
    boxes=(
        SeedBox(
            source_room="Open space",
            destination_room="Open space",
            title="Мониторы",
            is_fragile=True,
            items=_items(("Мониторы", 8), ("Клавиатуры", 8)),
        ),
        SeedBox(
            source_room="Open space",
            destination_room="Open space",
            title="Канцелярия",
            items=_items(("Степлеры", 4), ("Блокноты", 30), "Маркерная доска"),
        ),
        SeedBox(
            source_room="Переговорная",
            destination_room="Переговорная",
            title="Проектор",
            is_fragile=True,
            priority=Priority.HIGH,
            items=_items("Проектор", "Пульт", "Экран"),
        ),
    ),
    tasks=(
        SeedTask(title="Уведомить арендодателя", phase=TaskPhase.BEFORE),
        SeedTask(title="Заказать новые бейджи", phase=TaskPhase.BEFORE, days_from_move=-14),
        SeedTask(title="Настроить сеть и принтеры", phase=TaskPhase.AFTER, days_from_move=1),
        SeedTask(title="Обновить адрес на сайте", phase=TaskPhase.AFTER, days_from_move=3),
    ),
)

_STORAGE = SeedProject(
    name="Вещи на хранение",
    days_until_move=90,
    time_zone="Asia/Yekaterinburg",
    source_rooms=("Квартира",),
    destination_rooms=("Склад",),
    unpacked=(
        SeedUnpackedItems(
            source_room="Квартира",
            items=_items("Велотренажёр", ("Чемоданы", 3), "Ковёр", "Палатка"),
        ),
    ),
)

_THINGS = (
    "Тарелка", "Чашка", "Ложка", "Книга", "Футболка", "Свитер", "Полотенце", "Лампа",
    "Кабель", "Игрушка", "Тетрадь", "Ваза", "Рамка", "Подушка", "Плед", "Кастрюля",
    "Нож", "Часы", "Зарядка", "Фонарик",
)  # fmt: skip


def _warehouse_boxes(
    count: int, per_box: int, source_rooms: tuple[str, ...], destination_rooms: tuple[str, ...]
) -> tuple[SeedBox, ...]:
    """`count` boxes of `per_box` items each, spread over the rooms in turn."""
    boxes = []
    for n in range(count):
        source = source_rooms[n % len(source_rooms)]
        first = n * per_box
        boxes.append(
            SeedBox(
                source_room=source,
                destination_room=destination_rooms[n % len(destination_rooms)],
                title=f"Коробка {n + 1}: {source}",
                is_fragile=n % 7 == 0,
                priority=Priority(n % len(Priority)),
                items=tuple(
                    SeedItem(
                        name=f"{_THINGS[(first + k) % len(_THINGS)]} {first + k + 1}",
                        quantity=1 + k % 5,
                    )
                    for k in range(per_box)
                ),
            )
        )
    return tuple(boxes)


_WAREHOUSE_SOURCE_ROOMS = tuple(f"Комната {n}" for n in range(1, 9))
_WAREHOUSE_DESTINATION_ROOMS = tuple(f"Зона {n}" for n in range(1, 9))

_WAREHOUSE = SeedProject(
    name="Склад: 120 коробок",
    days_until_move=40,
    time_zone="Europe/Moscow",
    origin_address="Москва, Складочная ул., 1",
    destination_address="Москва, Варшавское ш., 100",
    source_rooms=_WAREHOUSE_SOURCE_ROOMS,
    destination_rooms=_WAREHOUSE_DESTINATION_ROOMS,
    boxes=_warehouse_boxes(120, 9, _WAREHOUSE_SOURCE_ROOMS, _WAREHOUSE_DESTINATION_ROOMS),
    unpacked=tuple(
        SeedUnpackedItems(
            source_room=room,
            priority=Priority(n % len(Priority)),
            items=tuple(
                SeedItem(name=f"{_THINGS[k % len(_THINGS)]} без коробки {n * 10 + k + 1}")
                for k in range(10)
            ),
        )
        for n, room in enumerate(_WAREHOUSE_SOURCE_ROOMS[:6])
    ),
    tasks=tuple(
        SeedTask(
            title=f"Задача {n + 1}",
            phase=(TaskPhase.BEFORE, TaskPhase.DURING, TaskPhase.AFTER)[n % 3],
            days_from_move=None if n % 5 == 0 else n % 3 * 3 - 10,
        )
        for n in range(40)
    ),
)

_FINISHED = SeedProject(
    name="Переезд завершён",
    days_until_move=-21,
    time_zone="Europe/Moscow",
    origin_address="Тула, пр. Ленина, 7",
    destination_address="Москва, ул. Профсоюзная, 56",
    source_rooms=("Кухня", "Гостиная", "Спальня"),
    destination_rooms=("Кухня", "Гостиная", "Спальня"),
    is_finished=True,
    boxes=(
        SeedBox(
            source_room="Кухня",
            destination_room="Кухня",
            title="Посуда",
            is_fragile=True,
            items=_items(("Тарелки", 8), ("Стаканы", 6), "Чайник"),
        ),
        SeedBox(
            source_room="Гостиная",
            destination_room="Гостиная",
            title="Книги",
            items=_items(("Книги", 30), "Настольная игра"),
        ),
        SeedBox(
            source_room="Спальня",
            destination_room="Спальня",
            title="Одежда",
            priority=Priority.HIGH,
            items=_items(("Футболки", 12), ("Носки", 15), "Пальто"),
        ),
        SeedBox(
            source_room="Спальня",
            destination_room="Гостиная",
            title="Документы",
            priority=Priority.FIRST_NECESSITY,
            items=_items("Паспорта", "Договор аренды", "Ноутбук"),
        ),
    ),
    tasks=(
        SeedTask(title="Нанять грузчиков", phase=TaskPhase.BEFORE, days_from_move=-14),
        SeedTask(title="Упаковать вещи", phase=TaskPhase.BEFORE, days_from_move=-3),
        SeedTask(title="Погрузить коробки", phase=TaskPhase.DURING, days_from_move=0),
        SeedTask(title="Подписать акт приёмки квартиры", phase=TaskPhase.DURING, days_from_move=0),
        SeedTask(title="Распаковать вещи", phase=TaskPhase.AFTER, days_from_move=5),
    ),
)

TEST_USERS: tuple[SeedUser, ...] = (
    SeedUser(username="test_1", projects=(_STUDIO,)),
    SeedUser(username="test_2", projects=(_APARTMENT, _COUNTRY_HOUSE)),
    SeedUser(username="test_3", projects=(_FAMILY_HOUSE, _OFFICE, _STORAGE)),
    SeedUser(username="test_4", projects=(_WAREHOUSE,)),
    SeedUser(username="test_5", projects=(_FINISHED,)),
)
