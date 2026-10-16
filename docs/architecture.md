# Архитектура

## Система

```text
client ──HTTP──▶ nginx :8000 ──▶ api (uvicorn, FastAPI) ──▶ PostgreSQL 18  источник истины
                 │                    │
                 │                    └──────────────────▶ Valkey 9       идемпотентность,
                 │                                          (Redis API)   rate limit (fail-open)
                 └─ X-Request-ID, X-Forwarded-For = $remote_addr, JSON 503 при отказе api
```

- **nginx** — единственная публичная точка входа ([ADR 0006](adr/0006-nginx-edge-and-rate-limiting.md)).
  Контейнер `api` порты наружу не публикует.
- **PostgreSQL** хранит все данные, включая сессии (`refresh_tokens`). Инварианты
  продублированы ограничениями в самой БД.
- **Redis** (сервер — Valkey, BSD-форк Redis с тем же протоколом) хранит только то, что
  можно потерять. При его отказе сервис продолжает работать в деградированном режиме
  ([ADR 0003](adr/0003-postgres-and-redis-fail-open.md)). В коде и настройках он везде
  называется Redis: это имя протокола и клиента `redis-py`.

## Слои

```text
           app.main ─ app.ioc ─ app.config          точка сборки (composition root)
                        │
                 app.presentation                    FastAPI: роуты, схемы, ошибки, middleware
                        │
                app.infrastructure                   SQLAlchemy, Redis, argon2/JWT, Prometheus
                        │
                 app.application                     use cases, порты, DTO; только stdlib
                        │
                   app.domain                        сущности, Value Objects; только stdlib
```

Зависимости направлены только вниз. Верхний слой может импортировать любой нижний, но не
наоборот ([ADR 0001](adr/0001-clean-architecture-and-ddd.md)).

| Слой | Что здесь | Чего здесь нет |
|---|---|---|
| `domain/` | `User`, `RefreshToken` (с `TokenFamilyId`); Value Objects `Username`, `RawPassword`, `PasswordHash`; `DomainError` | любые сторонние пакеты, ввод-вывод, время «сейчас» |
| `application/` | use cases (`RegisterUser`, `Login`, `RefreshTokens`…), сервисы (`CredentialsVerifier`, `TokenPairIssuer`, `IdempotentExecutor`), порты в `interfaces/`, DTO, `exceptions.py` | FastAPI, SQLAlchemy, Redis, pydantic, structlog; логирование только через stdlib `logging` |
| `infrastructure/` | ORM-модели, мапперы, репозитории, UoW, адаптеры Redis, argon2/JWT, метрики, настройка логов | бизнес-правила, HTTP |
| `presentation/` | роуты (`DishkaRoute`), pydantic-схемы (только форма запроса), контракт ошибок, `RequestContextMiddleware`, rate limit, `CurrentUser` | SQL, Redis, хэширование (только через порты) |
| `ioc/` | провайдеры Dishka: какой адаптер реализует какой порт и в каком scope | логика |

### Домен

- Value Objects — `@dataclass(frozen=True, slots=True)`. Нормализация (`strip`, `lower`) и
  валидация происходят в `__post_init__`. Нарушения приводят к подклассу `DomainError`.
  Pydantic в слое представления проверяет только структуру JSON.
- Сущности — `@dataclass(slots=True, kw_only=True, eq=False)`, наследники `Entity`. Сравнение
  и хэш вычисляются по типу и `id`. Идентификаторы — UUIDv7 (`domain/common/ids.py`):
  монотонные внутри процесса, поэтому порядок по `id` совпадает с порядком создания
  (`uuid.uuid7` из стандартной библиотеки Python 3.14).
- Доменные модули (`users`, `tokens`) друг друга не импортируют. Общие
  идентификаторы (`UserId`) лежат в shared kernel `domain/common`.

### Application

- Каждый сценарий — класс с `async def __call__(self, dto) -> ResultDTO`. Use case сам
  вызывает `await uow.commit()`. Сервисы (`TokenPairIssuer`, `CredentialsVerifier`,
  `IdempotentExecutor`) и хелперы (`lookup.py`) только готовят изменения и никогда не
  коммитят. Это проверяет AST-тест.
- Порты (`application/interfaces/`) — абстрактные классы: репозитории, хэшер, токены,
  rate limiter, хранилище идемпотентности, health probe.
- `application/common` — общие механизмы без бизнес-смысла: часы, UoW, пагинация
  (`PageRequest`/`Page`) и идемпотентность (`IdempotentExecutor`). Пагинацию и
  идемпотентность сейчас не использует ни один эндпоинт: они ждут первого бизнес-модуля и
  проверены unit-тестами и тестовым маршрутом в интеграционных тестах.
- Фичи `users`, `auth`, `health` независимы. Исключение одно: `auth` может
  использовать `users` (`AuthenticateUser` вызывает `GetUser`).

## Поток запроса

`PUT /api/v1/users/me/username`:

1. **nginx** генерирует или сохраняет `X-Request-ID`, перезаписывает `X-Forwarded-For`
   и проксирует запрос в `api:8000`.
2. **`RequestContextMiddleware`** привязывает `request_id` к контексту structlog. После ответа
   он пишет access-лог `http_request`, обновляет `http_requests_total` и
   `http_request_duration_seconds` по шаблону маршрута и добавляет `X-Request-ID` и
   накопленные заголовки (например, `X-RateLimit-*`) к любому ответу, включая ошибки.
3. **Dishka** открывает `Scope.REQUEST`: `AsyncSession`, UoW, репозитории и use cases
   создаются на запрос и делят одну сессию.
4. **`api_rate_limit`** считает запрос в Redis. Если Redis недоступен, запрос пропускается.
5. **`CurrentUser`** → `AuthenticateUser`: JWT → `user_id` → чтение пользователя из БД.
   Удалённый пользователь сразу получает 401.
6. **`ChangeUsername`**: `Username(...)` (валидация и нормализация) → `user.change_username` →
   `repo.update` (flush в `SAVEPOINT`, конфликт → `UsernameAlreadyTakenError`) → `uow.commit()`.
7. Небезопасный эндпоинт, которому нужны повторы без дублей, оборачивает use case в
   `IdempotentExecutor.run(...)` (`run_idempotent` в presentation). Ключ ставится в `pending`
   (`SET NX`), после успеха сохраняется JSON ответа (`completed`, 24 ч), при исключении ключ
   удаляется.
8. Ошибки любого слоя превращаются в `{"error": {...}}` в `presentation/http/errors.py`.

## Внедрение зависимостей (Dishka)

| Провайдер | Scope | Что предоставляет |
|---|---|---|
| `ConfigProvider` | APP | `Settings` (из контекста) и его секции, `RefreshTokenSettings`, `HttpSettings`, `RateLimitPolicy` |
| `DatabaseProvider` | APP / REQUEST | `AsyncEngine`, `async_sessionmaker`, `DatabaseHealthProbe` / `AsyncSession`, `AbstractUnitOfWork`, репозитории |
| `RedisProvider` | APP | `Redis` (пул соединений), `AbstractIdempotencyStore`, `AbstractRateLimiter`, `RedisHealthProbe` |
| `SecurityProvider` | APP | `AbstractPasswordHasher` (argon2id), `AbstractAccessTokenService` (JWT), `AbstractRefreshTokenGenerator` |
| `UseCaseProvider` | REQUEST | все use cases и сервисы приложения |
| `ObservabilityProvider` | APP | `ReadinessProbes`, `CheckReadiness` |

Граф проверяется при создании контейнера (`create_container`). Неразрешимая зависимость
обнаруживается при старте, а не на первом запросе. Ресурсы с `yield` (engine, Redis)
закрываются при `container.close()` в lifespan приложения.

Зачем отдельный пакет `ioc/`, а не `Depends` в роутерах, объясняет
[ADR 0002](adr/0002-dishka-ioc.md).

## Модель данных

Схема создаётся миграцией [0001](../migrations/versions/0001_initial.py); [0002](../migrations/versions/0002_drop_user_email.py) убирает `users.email`.

| Таблица | Ключевые ограничения |
|---|---|
| `users` | частичный уникальный индекс `uq_users_username_active` `WHERE is_active`; CHECK `username = lower(username)` |
| `refresh_tokens` | хранится только `token_hash` (SHA-256, CHECK длины 64, UNIQUE); `family_id` (сессия, индекс); `revoked_at`; FK на `users` с `ON DELETE CASCADE` |

### Состояния и переходы

| Объект | Состояния | Переходы | Где защищено |
|---|---|---|---|
| Пользователь | `active`, `deleted` (`is_active`) | регистрация → `active`; `SoftDeleteUser`: `active → deleted` (идемпотентно, все сессии отзываются); `RestoreUser`: `deleted → active` (409, если username уже занят) | методы `User`; частичные уникальные индексы `WHERE is_active`; CHECK нижнего регистра username |
| Refresh-токен | `active`, `revoked`, `expired` | вход → `active` в новой семье; ротация: предъявленный `active → revoked` и новый `active` в той же семье; повтор или logout: вся семья → `revoked`; смена пароля или удаление: все токены пользователя → `revoked`; `expired` наступает по времени | `RefreshToken.revoke` (первое время отзыва сохраняется); атомарный `UPDATE … WHERE revoked_at IS NULL RETURNING id`; UNIQUE и CHECK длины `token_hash` |

Переходы выполняются только методами сущностей и атомарными операциями репозиториев.
Состояние, которое нельзя получить законным путём (два активных пользователя с одним
username, хэш вместо сырого токена неверной длины), отклоняет сама БД. Стейт-машина нового
бизнес-модуля оформляется так же: матрица в домене, её копия в БД (CHECK или триггер) и
тест их согласованности ([ADR 0005](adr/0005-migrations.md)).

### Мягкое удаление пользователей

`SoftDeleteUser` ставит `is_active = false` и отзывает все сессии. Частичные индексы сразу
освобождают username для новой регистрации ([ADR 0008](adr/0008-soft-delete-and-savepoints.md)).
Репозиторий делает `flush()` внутри `SAVEPOINT`. Нарушение уникальности превращается в
`UsernameAlreadyTakenError` по имени индекса, а транзакция
запроса остаётся рабочей.

### Сессии (refresh-токены)

```text
login ─▶ новая семья F; (access JWT 15 мин, refresh = token_urlsafe(32)); в БД только sha256(refresh)
         хэш пароля с устаревшими параметрами argon2 тут же пересчитывается (rehash)
refresh(R из семьи F) ─▶ UPDATE refresh_tokens SET revoked_at = now
                         WHERE id = :id AND revoked_at IS NULL RETURNING id
              ├─ строка вернулась ─▶ новая пара токенов в той же семье F
              └─ пусто (R уже использован) ─▶ revoke_family(F), WARNING refresh_token_reused, 401
logout(любой R из F) ─▶ revoke_family(F)
```

Семья — это одна сессия: вход и все её ротации. Повтор использованного токена означает
утечку, поэтому отзывается вся семья: и вор, и владелец этой сессии входят заново, а другие
устройства пользователя продолжают работать. Смена пароля и мягкое удаление отзывают все
сессии пользователя. Access-токен проверяется на
каждом запросе чтением пользователя из БД: удалённый пользователь получает 401 сразу, не
дожидаясь истечения JWT ([ADR 0007](adr/0007-authentication-and-sessions.md)).

## Контроль архитектуры

[.importlinter](../.importlinter) задаёт пять контрактов:

1. слои `main → ioc → config → presentation → infrastructure → application → domain`;
2. `domain` и `application` не импортируют сторонние пакеты: fastapi, sqlalchemy, pydantic,
   redis, structlog, prometheus_client, dishka…;
3. доменные модули независимы друг от друга;
4. фичи application независимы, кроме `auth → users`;
5. presentation не обращается к `infrastructure.db/redis/security` напрямую, только через порты.

[tests/unit/test_architecture.py](../tests/unit/test_architecture.py) проверяет то, что
import-linter выразить не может:

- явные списки разрешённых импортов для каждого доменного модуля и фичи;
- «дом» каждой технологии: `sqlalchemy` только в `infrastructure/db` и `ioc/db_provider.py`,
  `redis` только в `infrastructure/redis`, и т.д.;
- ORM-модели только в `infrastructure/db/models/`;
- у каждого use case есть `async def __call__`;
- сервисы и хелперы не вызывают `commit`;
- сущности — slotted kw-only датаклассы с равенством по id.

Параметризованный тест-самопроверка доказывает, что правила срабатывают на нарушениях.

## Как добавить модуль

Пример: `projects`.

1. `domain/projects/`: сущность (наследник `Entity`), Value Objects, ошибки. Импорт
   разрешён только из `app.domain.common`; добавьте модуль в `DOMAIN_ALLOWED` в
   архитектурном тесте и в контракт `domain_modules`.
2. `application/interfaces/project_repository.py`: порт.
   `application/projects/`: `dto.py` и по файлу на use case. Добавьте фичу в
   `APPLICATION_FEATURE_DEPS` и в контракт `application_modules`.
3. `infrastructure/db/models/project.py`, `mappers/project.py`,
   `repositories/project.py`. Экспортируйте модель в `models/__init__.py`.
4. `make revision m="projects"`, затем проверьте миграцию и `make migrations-check`.
5. `ioc/`: репозиторий в `DatabaseProvider`, use cases в `UseCaseProvider`.
6. `presentation/http/schemas/projects.py`, `routes/projects.py`, подключение в `app.py`.
   Новые исключения добавьте в `ERROR_SPECS`; тест `test_every_concrete_error_has_its_own_code`
   напомнит, если забыть.
7. Тесты: фейк порта в `tests/fakes.py`, unit-тесты use cases, интеграционные тесты
   репозитория и API.

## Известные ограничения

- Пока Redis недоступен, каждый запрос ждёт таймаут Redis (200 мс, плюс один повтор) на
  каждую операцию. Rate limit и идемпотентность в это время не действуют. Улучшение —
  circuit breaker поверх `REDIS_FAILURES`.
- Fixed window пропускает до 2× лимита на стыке окон.
- Ключи идемпотентности хранятся в Redis: после потери данных Redis повтор может создать дубль.
- Два параллельных refresh одним токеном: второй считается повтором и завершает сессию
  (семью). Так строже; смягчить можно коротким grace-периодом для только что отозванного токена.
- Перехэширование происходит только при входе: пароли пользователей, которые давно не
  входили, остаются со старыми параметрами argon2.
- Маппер заново проверяет значения из БД через Value Objects. Если правила ужесточить
  (например, формат username), уже сохранённая строка, которая им не отвечает, перестанет
  читаться. Ужесточение правил сопровождайте миграцией или скриптом, который находит и
  исправляет такие данные.
- Миграция [0002](../migrations/versions/0002_drop_user_email.py) удаляет колонку `users.email`
  за один релиз, без expand/contract: код предыдущего релиза пишет в неё и на новой схеме не
  работает. Допустимо, пока шаблон не выкатывался на прод; иначе делите на два релиза (сначала
  `email` nullable, затем удаление). `downgrade()` возвращает колонку с заглушками
  `<id>@removed.invalid`: настоящие адреса не восстанавливаются.
