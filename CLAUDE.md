# CLAUDE.md

## Что это за проект

Эталонный асинхронный бэкенд: Clean Architecture + DDD в ядре и эксплуатационная обвязка
вокруг него (nginx edge, Redis fail-open, Prometheus, structlog, миграции с двусторонней
проверкой, CI/CD). Готовый домен — пользователи (сессии с семьями refresh-токенов,
перехэширование паролей, soft delete). Модули продукта добавляются рядом по чек-листу
`docs/architecture.md#как-добавить-модуль`, сохраняя правила ниже.

Стек: Python 3.14.8, FastAPI, Dishka, SQLAlchemy 2.1 async + asyncpg, Alembic, PostgreSQL 18.6,
Valkey 9.1 (протокол Redis, клиент redis-py), argon2-cffi, PyJWT, structlog,
prometheus-client, nginx 1.30, uv 0.13, pytest + testcontainers, ruff, mypy strict,
import-linter. Все версии закреплены точно: `==` в `pyproject.toml` + `uv.lock`, полные теги
образов. Меняя версию, меняйте её везде (compose, CI, `tests/integration/conftest.py`).

Документация на русском, код, комментарии и docstrings на английском.

Ориентиры: [README.md](README.md), [docs/architecture.md](docs/architecture.md),
[docs/api.md](docs/api.md), [docs/observability.md](docs/observability.md),
[docs/release.md](docs/release.md), [docs/adr/](docs/adr/).

## Критерии жюри (backend)

Шаблон готовится к хакатону PROD (Т-Банк, Центральный университет, ВШЭ). Первоисточник —
`Критерии.pdf`, раздел «Критерии жюри — Backend» (стр. 1–3). Цель — каждый критерий на 100%,
и после любого изменения он должен оставаться закрытым.

| # | Критерий (якорь) | 100% по критериям | Где закрыто | Что нельзя сломать |
|---|---|---|---|---|
| 1 | `backend-api-и-серверные-контракты` | контракты последовательны и однозначны в основных и пограничных сценариях | `presentation/http/errors.py` (`ERROR_SPECS`), `schemas/`, `docs/api.md`, `tests/unit/presentation/test_openapi.py` | единый конверт ошибок (и у nginx), стабильные `code`, `error_doc(...)` в `responses=`, `/api/v1`, каждая операция классифицирована в `test_openapi.py` |
| 2 | `backend-архитектура-сервиса` | архитектура понятна и выдержана во всех ключевых частях | слои `src/app/*`, `.importlinter`, `tests/unit/test_architecture.py`, `tests/unit/ioc/`, ADR 0001–0002 | направление зависимостей, чистый stdlib в domain/application, use case = класс, commit только в use case |
| 3 | `backend-архитектура-системы` | система последовательна и обоснована требованиями кейса | `docs/architecture.md#система`, `docker-compose*.yml`, `docker/nginx/`, ADR 0003, 0006 | у `api` нет `ports:`, диаграмма и описание соответствуют compose |
| 4 | `backend-архитектурные-компромиссы-и-ограничения` | выбор структуры, данных и контрактов объяснён через кейс и слабые места | `docs/adr/`, «Известные ограничения» в `architecture.md` | новое решение = новый ADR (или пересмотр с датой), новое ограничение = строка в списке |
| 5 | `backend-безопасное-добавление-изменений-без-поломки-сценария` | собранный сценарий сохраняется при каждом изменении | CI-стадия `check`, pre-commit, регрессионные тесты | `make check` зелёный перед каждым коммитом; стабильность важнее объёма |
| 6 | `backend-выпуск-изменений-и-обновление-схемы-данных` | выпуск и миграции предсказуемы, без ручных ритуалов | `migrations/`, сервис `migrate`, jobs `migrations`/`deploy`, `docs/release.md` | миграции не запускаются из приложения; expand/contract; рабочий `downgrade()`; последовательные номера |
| 7 | `backend-модель-данных-и-переходы-состояний` | данные и переходы согласованы и предсказуемы в критических точках | `architecture.md#состояния-и-переходы`, сущности домена, CHECK/частичные индексы, атомарный отзыв токенов, `test_migrations.py` | инварианты и в домене, и в БД; переходы только методами сущностей; стейт-машина нового модуля — в домене, в БД и с тестом согласованности |
| 8 | `backend-наблюдаемость-и-диагностика-сервиса` | проблема видна и локализуется до полной потери сценария | structlog JSON + `request_id` (сквозь nginx), `/health/*`, `/metrics`, `docs/observability.md` | ключевые и security-события логируются; метки метрик по шаблону маршрута; новая зависимость добавляется в `ReadinessProbes` |
| 9 | `backend-проверка-бизнес-логики-и-контрактов` | критичные правила и контракты проверяются последовательно, а не на словах | `tests/unit` (домен, use cases, контракты, проводка DI), `tests/integration` (БД, Redis, E2E) | у каждого эндпоинта тесты на успех, валидацию, границу и ошибку; контракт — в `test_openapi.py` |

Смежные треки: фронтенд и мобильное приложение оцениваются по поведению при деградации,
синхронизации с API и удобству контракта для мобильного клиента. Поэтому важны:
- CORS (`CORS_ORIGINS`);
- `Idempotency-Key` для повторов;
- стабильные `code` в ошибках и `Retry-After`;
- JSON-ответ nginx, когда api недоступен;
- предсказуемая ротация токенов: повтор старого refresh завершает только эту сессию.

Мобильным клиентам нужен HTTPS: nginx работает по HTTP, TLS снимает внешний балансировщик
(ADR 0006). Продуктовые критерии (приоритет базового сценария, проверка критичных мест)
означают: сначала надёжный основной путь, потом дополнительные функции.

## Обязательные правила

### Слои (проверяются `lint-imports` и `tests/unit/test_architecture.py`)

- `domain` и `application` — только стандартная библиотека. Никаких pydantic, sqlalchemy,
  fastapi, redis, structlog, dishka. В application логируйте через `logging.getLogger(__name__)`
  с `extra={...}`: structlog подхватит поля.
- Доменные модули (`users`, `tokens`) не импортируют друг друга. Общее лежит в
  `domain/common` (например, `UserId`).
- Фичи application независимы, кроме `auth → users`. Новая зависимость между фичами
  оформляется осознанно: в `APPLICATION_FEATURE_DEPS` и в `.importlinter`.
- Технология живёт только в своём адаптере: SQLAlchemy в `infrastructure/db` (и
  `ioc/db_provider.py`), Redis в `infrastructure/redis`, argon2/jwt в `infrastructure/security`,
  prometheus_client в `infrastructure/metrics`.
- presentation обращается к хранилищам только через use cases и порты, без прямых импортов
  `infrastructure.db/redis/security`.
- `app.config` импортируют только `ioc`, `main` и `migrations/env.py`. Остальным слоям
  настройки передаются плоскими объектами через Dishka.

### Domain и application

- Value Objects (`frozen=True, slots=True`) сами нормализуют и валидируют вход и бросают
  подкласс `DomainError`. Pydantic-схемы проверяют только форму JSON.
- Сессия = семья refresh-токенов (`family_id`): повтор использованного токена и logout
  отзывают семью, смена пароля и удаление — все сессии пользователя.
- Сущности наследуют `Entity` (`@dataclass(eq=False, kw_only=True, slots=True)`), равенство
  по id, id — `new_id()` (UUIDv7). Время передаётся в методы явно (`now=`).
- Один use case — один класс с `async def __call__(self, dto) -> DTO` в отдельном файле.
  Транзакцией управляет use case: `await self._uow.commit()`. Сервисы (`services.py`),
  хелперы (`lookup.py`) и `application/common` **никогда** не коммитят.
- Use cases возвращают DTO (`*DTO.from_entity`), а не сущности.
- Новое исключение — подкласс `DomainError` или `ApplicationError` плюс запись в
  `ERROR_SPECS` (`presentation/http/errors.py`) со стабильным `code`. Тест проверит.

### Infrastructure

- ORM-модели только в `infrastructure/db/models/` и экспортируются в `models/__init__.py`
  (иначе Alembic их не увидит). Перевод ORM ↔ домен — только через мапперы
  `to_domain`/`to_model`.
- Репозитории не коммитят. Уникальность проверяется `flush()` в `begin_nested()`, имя
  constraint превращается в прикладную ошибку (см. `SqlAlchemyUserRepository`).
- Любой вызов Redis обёрнут в `except REDIS_FAILURES` + `report_redis_failure(op, error)`
  (fail-open). Ничего, без чего сервис не может работать, в Redis не хранится.

### Presentation

- Роутеры с `route_class=DishkaRoute`, зависимости через `FromDishka[...]`. Функции для
  `Depends` помечаются `@inject`.
- Новые эндпоинты бизнес-логики добавляются под `/api/v1`. Ошибки документируются через
  `error_doc(...)` в `responses=`.
- Заголовки, которые должны попасть и в ответы с ошибкой, добавляются через
  `add_response_headers(request, ...)`.

### Миграции

- Только `make revision m="..."` (последовательный номер), затем ручная проверка
  сгенерированного файла.
- Миграции не импортируют `app.*`: значения переписываются литералами.
- Правило, продублированное в БД (CHECK, триггер), меняется в домене **и** в новой миграции.
- Изменения обратно совместимы с кодом предыдущего релиза (expand/contract).

## Команды

```bash
make install          # зависимости + pre-commit
make test-unit        # быстрые тесты без Docker (пара секунд)
make test             # все тесты, интеграционные в testcontainers
make lint             # ruff + format check + mypy strict + import-linter
make fmt              # автоисправление
make check            # lint + test + migrations-check (как CI)
make up / make down   # весь стек на :8000
```

## Как писать тесты

- Use case тестируйте unit-тестом на фейках из [tests/fakes.py](tests/fakes.py) (фикстуры в
  `tests/unit/conftest.py`). Фейк нового порта добавляется туда же. Фейки-репозитории
  возвращают копии: забытый `update()` тест поймает.
- Репозитории, миграции и Redis тестируются в `tests/integration/` на настоящих сервисах.
  Метка `integration` ставится автоматически по пути. Фикстура `session_maker` очищает
  таблицы после теста, `redis` — Redis.
- E2E: фикстура `client` или `client_factory(**settings_sections)` для другой конфигурации
  (лимиты, недоступный Redis через `UNREACHABLE_REDIS_URL`). Хелпер `signed_up()` регистрирует
  пользователя и логинится.
- Логи проверяются фикстурой `log_events()`: она возвращает список событий-словарей.
- Механизмы без бизнес-эндпоинта (идемпотентность, маршрут с параметром для метрик)
  проверяются через `client_factory(configure_app=add_test_routes)` из `tests/routes.py`.
- Проводка Dishka и опубликованный OpenAPI проверяются без Docker (`tests/unit/ioc/`,
  `tests/unit/presentation/test_openapi.py`). Новую операцию API отнесите к `PROTECTED` или
  `PUBLIC` в `test_openapi.py`; новый use case подхватится тестом контейнера сам.

## Чек-лист перед завершением задачи

- [ ] `make check` зелёный: ruff, mypy strict, import-linter, все тесты, migrations-check.
- [ ] Новые ошибки есть в `ERROR_SPECS` и в таблице `docs/api.md`.
- [ ] Новые метрики и события логов описаны в `docs/observability.md`.
- [ ] Архитектурное решение, которое меняет правила, оформлено ADR в `docs/adr/`.

## Чек-лист перед защитой

- [ ] `make check` зелёный, последний пайплайн в GitLab зелёный.
- [ ] `docs/architecture.md`: схема системы, слои, состояния и переходы соответствуют коду.
- [ ] `docs/api.md`: таблица эндпоинтов и кодов ошибок совпадает с `/docs`.
- [ ] ADR объясняют выбор через требования кейса, ограничения перечислены честно.
- [ ] `docs/release.md`: показан путь «коммит → прод» и откат, есть демонстрация деплоя.
- [ ] `docs/observability.md`: показано, как по `request_id` найти запрос в логах nginx и
  api и как выглядит `degraded`.
- [ ] Основной сценарий проходит end-to-end на задеплоенной версии (`/health/ready` → `version`).
- [ ] Раздел «Использование ИИ» в README заполнен командой: исходная версия, права на
  заимствованный код, что написано с ИИ, а что вручную.
