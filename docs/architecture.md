# Архитектура

## Система

```text
client ──HTTPS──▶ nginx :443 ──▶ api (uvicorn, FastAPI) ──▶ PostgreSQL 18  источник истины
       (prod)     │  :80 → 308       │
                  │                  ├──────────────────▶ Valkey 9       идемпотентность,
                  │                  │                     (Redis API)   rate limit (fail-open)
                  │                  └──HTTPS, исходящий──▶ LLM-прокси Spirit  сканирование вещей
                  │                                        (OpenAI API)  по фото (503 при отказе)
                  ├─ X-Request-ID, X-Forwarded-For = $remote_addr, JSON 503 при отказе api
                  └─ сертификат ◀── certbot (Let's Encrypt, webroot через :80, продление)

scheduler (taskiq scheduler) ──▶ Valkey: список taskiq:notifications   сигнал «пора» каждые 15 с
                                       │
worker (taskiq worker) ◀───────────────┘
  │  без портов   ──▶ PostgreSQL   созревшие notifications, FOR UPDATE SKIP LOCKED
  └──HTTPS, исходящий (Web Push, VAPID)──▶ push-сервисы браузеров ──▶ service worker клиента
api, worker ── общий том vapid: ключ VAPID (api отдаёт публичную часть, worker подписывает)

stdout контейнеров с меткой sage.logs ──Docker API──▶ vector ──HTTPS, исходящий──▶ Sage
  (api, worker, scheduler, migrate, nginx, certbot, redis)          Logs Collector (прод)
```

- **nginx** — единственная публичная точка входа ([ADR 0006](adr/0006-nginx-edge-and-rate-limiting.md)).
  Пути `/api`, `/health`, `/docs` идут в api, остальное — в фронтенд (`:3000` на хосте).
  Контейнер `api` порты наружу не публикует. В проде nginx завершает TLS на `:443`, а `:80`
  отдаёт ACME challenge и редиректит на https. Локально стек работает по HTTP на `:8000`.
- **certbot** выпускает и продлевает сертификат Let's Encrypt для `DOMAIN` и кладёт его в
  том, который читает nginx ([ADR 0009](adr/0009-tls-letsencrypt-certbot.md)). Сервис нужен
  только в проде.
- **scheduler** и **worker** — тот же образ, команды taskiq
  ([ADR 0012](adr/0012-notification-worker.md)). `scheduler` каждые 15 с кладёт в Redis сигнал
  `deliver_due_notifications`; он должен быть один. `worker` по сигналу отправляет созревшие
  уведомления из таблицы `notifications` через Web Push. Очередь и источник истины — таблица,
  сообщение в Redis лишь будит воркер. Портов сервисы не публикуют; `worker` наружу ходит только
  исходящим HTTPS к push-сервисам браузеров (Google, Mozilla, Apple). Ключ VAPID лежит в томе
  `vapid`, общем с api.
- **PostgreSQL** хранит все данные, включая сессии (`refresh_tokens`) и очередь уведомлений.
  Инварианты продублированы ограничениями в самой БД.
- **Redis** (сервер — Valkey, BSD-форк Redis с тем же протоколом) хранит только то, что
  можно потерять: ключи идемпотентности, счётчики rate limit и сигналы воркеру. При его отказе
  API продолжает работать в деградированном режиме
  ([ADR 0003](adr/0003-postgres-and-redis-fail-open.md)), а уведомления задерживаются до его
  возвращения. В коде и настройках он везде
  называется Redis: это имя протокола и клиента `redis-py`.
- **LLM-прокси Spirit** — внешний OpenAI-совместимый API (`POST /v1/chat/completions`),
  через который api отправляет фото vision-модели `openrouter/claude-haiku-5-5`
  ([ADR 0015](adr/0015-photo-scanning-with-llm.md)). Это вспомогательная зависимость: при её
  отказе только `scanItems` отвечает 503, `/health/ready` показывает `llm: degraded`. Без
  `LLM__API_KEY` сканирование выключено. Для этого маршрута nginx пропускает тело до 6 МБ
  и ждёт ответа до 45 с, для остальных — 1 МБ и 30 с.

- **vector** (только прод) читает логи контейнеров через Docker API, добавляет поля, которые
  требует Sage (`env`, `system`, `inst`, `@timestamp`, `level`), и отправляет их в Logs
  Collector Sage ([ADR 0017](adr/0017-logs-in-sage.md)). Это вспомогательный сервис: от него
  ничего не зависит, без `SAGE__API_KEY` он не запускается.

## Слои

```text
              app.main | app.worker                  точки сборки (composition roots):
              app.ioc ─ app.config                   HTTP-сервис и воркер уведомлений
                        │
                 app.presentation                    FastAPI: роуты, схемы, ошибки, middleware
                        │
                app.infrastructure                   SQLAlchemy, Redis, argon2/JWT, Web Push, Prometheus
                        │
                 app.application                     use cases, порты, DTO; только stdlib
                        │
                   app.domain                        сущности, Value Objects; только stdlib
```

Зависимости направлены только вниз. Верхний слой может импортировать любой нижний, но не
наоборот ([ADR 0001](adr/0001-clean-architecture-and-ddd.md)).

| Слой | Что здесь | Чего здесь нет |
|---|---|---|
| `domain/` | `User`, `RefreshToken` (с `TokenFamilyId`); агрегаты переезда `Project`, `Room`, `Box`, `Item`, `Task`, `Notification`, `PushSubscription`, `AuditEntry`; Value Objects; `DomainError` | любые сторонние пакеты, ввод-вывод, время «сейчас» |
| `application/` | use cases (`RegisterUser`, `Login`, `RefreshTokens`, `CreateProject`, `DeliverNextNotification`…), сервисы (`CredentialsVerifier`, `TokenPairIssuer`, `IdempotentExecutor`, `MovingReminderScheduler`), порты в `interfaces/`, DTO, `exceptions.py` | FastAPI, SQLAlchemy, Redis, pydantic, structlog; логирование только через stdlib `logging` |
| `infrastructure/` | ORM-модели, мапперы, репозитории, UoW, адаптеры Redis, argon2/JWT, Web Push (pywebpush, ключи VAPID), брокер taskiq (`tasks/`), метрики, настройка логов | бизнес-правила, HTTP |
| `presentation/` | роуты (`DishkaRoute`), pydantic-схемы (только форма запроса), контракт ошибок, `RequestContextMiddleware`, rate limit, `CurrentUser` | SQL, Redis, хэширование (только через порты) |
| `ioc/` | провайдеры Dishka: какой адаптер реализует какой порт и в каком scope | логика |
| `worker/` | точка сборки воркера: задача taskiq `deliver_due_notifications`, планировщик, `drain`, heartbeat; `entry.py` — цели для CLI `taskiq` | бизнес-правила (только вызов use case) |

### Домен

- Value Objects — `@dataclass(frozen=True, slots=True)`. Нормализация (`strip`, `lower`) и
  валидация происходят в `__post_init__`. Нарушения приводят к подклассу `DomainError`.
  Pydantic в слое представления проверяет только структуру JSON.
- Сущности — `@dataclass(slots=True, kw_only=True, eq=False)`, наследники `Entity`. Сравнение
  и хэш вычисляются по типу и `id`. Идентификаторы — UUIDv7 (`domain/common/ids.py`):
  монотонные внутри процесса, поэтому порядок по `id` совпадает с порядком создания
  (`uuid.uuid7` из стандартной библиотеки Python 3.14).
- Доменные модули (`users`, `tokens`, `projects`, `inventory`, `tasks`, `notifications`,
  `audit`) друг друга не импортируют. Общее лежит в shared kernel `domain/common`:
  идентификаторы (`UserId`, `ProjectId`, `RoomId`, `BoxId`, `ItemId`, `TaskId`),
  `ReminderOffset` (его выбирают у задачи и несёт уведомление) и нормализация текста.
- Агрегаты и таблицы сопоставлены в [db-design.md](db-design.md#14-агрегаты-домена), границы
  объясняет [ADR 0010](adr/0010-moving-domain-aggregates.md). Матрицы переходов
  (`BOX_TRANSITIONS`, `ITEM_TRANSITIONS`, `NOTIFICATION_TRANSITIONS`) лежат рядом с сущностями,
  их копия в БД — триггеры миграции 0004; тест перебирает все пары и сверяет одно с другим.

### Application

- Каждый сценарий — класс с `async def __call__(self, dto) -> ResultDTO`. Use case сам
  вызывает `await uow.commit()`. Сервисы (`TokenPairIssuer`, `CredentialsVerifier`,
  `IdempotentExecutor`, `MovingReminderScheduler`) и хелперы (`lookup.py`) только готовят
  изменения и никогда не коммитят. Это проверяет AST-тест.
- Порты (`application/interfaces/`) — абстрактные классы: репозитории (пользователи, токены,
  проекты, комнаты, коробки, вещи, уведомления, push-подписки, журнал), модели чтения
  `AbstractProjectReader` и `AbstractNotificationContextReader`, хэшер, токены, rate limiter,
  хранилище идемпотентности, отправка Web Push (`AbstractPushSender`), ключи VAPID
  (`AbstractPushKeys`), health probe.
- **Read model** — порт чтения внутри фичи (`inventory/read_model.py`,
  `summaries/read_model.py`). Экранам нужны представления из нескольких агрегатов сразу
  (коробка с комнатой, создателем и превью содержимого), и собирать их из агрегатов значило
  бы делать запрос на каждый объект. Use cases меняют данные через репозитории, а отдают ответ
  через read model, который возвращает готовые DTO за несколько запросов на страницу
  ([ADR 0013](adr/0013-inventory-read-model.md)). Порт возвращает DTO фичи, поэтому лежит в
  ней, а не в `interfaces/` (общие части application фичи не импортируют).
- `application/common` — общие механизмы без бизнес-смысла: часы, UoW, пагинация по offset
  (`PageRequest`/`Page`) и по курсору (`CursorPageRequest`/`CursorPage`), маркер `UNSET`
  для частичных обновлений и идемпотентность (`IdempotentExecutor`).
- Фичи `users`, `auth`, `health`, `projects`, `notifications`, `inventory`, `summaries`
  независимы. Исключений два: `auth` использует `users` (`AuthenticateUser` вызывает
  `GetUser`), а `summaries` использует `inventory` (проверку участия и DTO ссылок на комнаты и
  коробки). Третье исключение — `seed`: `SeedTestUsers` создаёт тестовые данные через
  настоящие use cases `users`, `projects`, `inventory` и `tasks`.
- **Тестовые аккаунты.** При `SEED_TEST_USERS=true` (локально по умолчанию `.env.example`; в prod только с
  собственным `SEED_TEST_PASSWORD`, [ADR 0018](adr/0018-test-accounts-in-prod.md))
  `create_app` на старте вручную достаёт `SeedTestUsers` из контейнера и вызывает его: пять
  аккаунтов (`test_empty_1`, `test_empty_2` пустые; `test_small`, `test_medium`,
  `test_large` с проектами разного размера), данные лежат в `seed/catalog.py`, пароль — в
  `SEED_TEST_PASSWORD` (без него публичный из каталога).
  Повторный запуск пропускает существующие аккаунты; сбой не мешает api стартовать.
- **Проекты** ([ADR 0011](adr/0011-projects-api.md)). Изменяющий use case берёт проект с
  блокировкой строки (`get_by_id(for_update=True)`) через `get_member_project_or_raise`:
  не-участник получает тот же `ProjectNotFoundError`, что и для несуществующего проекта.
  Изменения идут через агрегат и репозиторий, ответ после коммита читает
  `AbstractProjectReader` (username владельца, `created_at`/`updated_at` из БД).
  Напоминания о переезде (`MovingReminderScheduler`) ставятся в outbox `notifications` в той
  же транзакции на 09:00 по `time_zone` проекта.
- **Уведомления** ([ADR 0012](adr/0012-notification-worker.md)). Подписки браузеров ведут
  `SubscribeToPush` (upsert по `endpoint`) и `UnsubscribeFromPush`. Отправку выполняет
  `DeliverNextNotification`, его вызывает воркер, а не HTTP.
- `inventory` проверяет участие в проекте через порт проектов. Для чтения не-участнику
  отвечает 404 того объекта, который он просил, а не 403. Порты проектов, уведомлений и
  журнала (`AbstractProjectRepository`, `AbstractNotificationRepository`, `AbstractAuditLog`)
  пока содержат только то, что нужно инвентарю.
- **Сводки** ([ADR 0017](adr/0014-summaries-read-model.md)). Use cases `summaries`
  (`GetProjectSummary`, `GetLoadingChecklist`, `GetUnpackingProgress`, `GetBoxLabels`)
  проверяют участие через `project_of_member` и читают `AbstractSummaryReadModel`. Агрегаты
  считает SQL, по одному выражению на ответ, а правило «комната погружена» лежит в домене
  (`is_source_room_loaded`).

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
7. Небезопасный эндпоинт, которому нужны повторы без дублей (сейчас `POST /projects`),
   оборачивает use case в `IdempotentExecutor.run(...)` (`run_idempotent` в presentation). Ключ ставится в `pending`
   (`SET NX`), после успеха сохраняется JSON ответа (`completed`, 24 ч), при исключении ключ
   удаляется.
8. Ошибки любого слоя превращаются в `{"error": {...}}` в `presentation/http/errors.py`.

## Поток уведомления

1. Use case изменения (например, `SetProjectReminders`) ставит строку `PENDING` в
   `notifications` в своей транзакции.
2. **`scheduler`** раз в `WORKER__INTERVAL_SECONDS` (15 с) кладёт сообщение
   `deliver_due_notifications` в список Redis `taskiq:notifications` (`WORKER__QUEUE_NAME`).
3. **`worker`** (`app.worker`) берёт сообщение и запускает `drain`: `Scope.REQUEST` на каждое
   уведомление, до `WORKER__BATCH_SIZE` (100) за запуск. После успешного запуска обновляет
   heartbeat-файл.
4. **`DeliverNextNotification`**: `claim_next_due` (`FOR UPDATE SKIP LOCKED`) →
   `compose_message` по текущим данным (`AbstractNotificationContextReader`; причины нет →
   `CANCELLED`) → `AbstractPushSender.send` на каждую активную подписку получателя →
   `mark_sent` / `mark_skipped` / `record_failure` → `uow.commit()` → лог `notification_processed`.
5. **`WebPushSender`** шифрует payload (aes128gcm), подписывает запрос VAPID и отправляет его
   push-сервису; 404/410 → подписка деактивируется, остальные ошибки → повтор с задержкой.

## Внедрение зависимостей (Dishka)

| Провайдер | Scope | Что предоставляет |
|---|---|---|
| `ConfigProvider` | APP | `Settings` (из контекста) и его секции, `RefreshTokenSettings`, `HttpSettings`, `RateLimitPolicy` |
| `DatabaseProvider` | APP / REQUEST | `AsyncEngine`, `async_sessionmaker`, `DatabaseHealthProbe`, `NotificationLagProbe` / `AsyncSession`, `AbstractUnitOfWork`, репозитории, `AbstractProjectReader`, `AbstractNotificationContextReader` |
| `RedisProvider` | APP | `Redis` (пул соединений), `AbstractIdempotencyStore`, `AbstractRateLimiter`, `RedisHealthProbe` |
| `SecurityProvider` | APP | `AbstractPasswordHasher` (argon2id), `AbstractAccessTokenService` (JWT), `AbstractRefreshTokenGenerator` |
| `PushProvider` | APP | `VapidKeyStore` (`AbstractPushKeys`), `AbstractPushSender` (`WebPushSender` с сессией aiohttp) |
| `UseCaseProvider` | REQUEST | все use cases и сервисы приложения |
| `ObservabilityProvider` | APP | `ReadinessProbes`, `CheckReadiness` |

Граф проверяется при создании контейнера (`create_container`). Неразрешимая зависимость
обнаруживается при старте, а не на первом запросе. Ресурсы с `yield` (engine, Redis, HTTP-сессия
`WebPushSender`) закрываются при `container.close()`: в lifespan приложения и на `WORKER_SHUTDOWN`
воркера. Воркер собирает тот же контейнер на `WORKER_STARTUP` (планировщик — нет); ключ VAPID и HTTP-сессия создаются при первом
использовании, поэтому в api отправитель ничего не открывает.

Зачем отдельный пакет `ioc/`, а не `Depends` в роутерах, объясняет
[ADR 0002](adr/0002-dishka-ioc.md).

## Модель данных

Схема создаётся миграцией [0001](../migrations/versions/0001_initial.py); [0002](../migrations/versions/0002_drop_user_email.py) убирает `users.email`;
[0003](../migrations/versions/0003_add_users_updated_at.py) добавляет служебный `users.updated_at`
(ставят БД и ORM, домен его не хранит); [0004](../migrations/versions/0004_moving_domain_tables.py)
создаёт таблицы домена переезда; [0005](../migrations/versions/0005_task_phase_box_title_and_fragility_.py)
добавляет этап задачи, описание и «хрупкое» у коробки и напоминания о дате переезда (сверка
с фронтендом); [0006](../migrations/versions/0006_project_time_zone_and_moving_day_reminder.py)
добавляет `projects.time_zone` и разрешает напоминание в день переезда (смещение 0). Полный список ограничений — в
[db-design.md](db-design.md#11-индексы-и-ограничения). ORM ↔ домен переводят мапперы
`infrastructure/db/mappers/`; дочерние строки агрегата (участники и напоминания проекта,
ответственные и напоминания задачи) — связи `relationship(cascade="all, delete-orphan", lazy="selectin")`
внутри агрегата, так что `merge()` корня синхронизирует их. Связей между агрегатами в моделях
нет, только внешние ключи.

| Таблица | Ключевые ограничения |
|---|---|
| `users` | частичный уникальный индекс `uq_users_username_active` `WHERE is_active`; CHECK `username = lower(username)` |
| `refresh_tokens` | хранится только `token_hash` (SHA-256, CHECK длины 64, UNIQUE); `family_id` (сессия, индекс); `revoked_at`; FK на `users` с `ON DELETE CASCADE` |
| `projects`, `project_collaborators`, `project_reminders` | CHECK `qr_code_strategy`; `time_zone varchar(64)`; PK участника `(project_id, user_id)`; CHECK `offset_days BETWEEN 0 AND 365` |
| `rooms`, `boxes`, `items` | составные FK `(project_id, …)` не дают сослаться на объект другого проекта; частичные UNIQUE номера коробки и имени комнаты `WHERE is_active`; CHECK `(state = 'IN_BOX') = (box_id IS NOT NULL)`; триггеры переходов статуса коробки и состояния вещи; GIN FTS по `items.name` |
| `tasks`, `task_collaborators`, `task_reminders` | CHECK `is_done = (done_at IS NOT NULL)`; CHECK `phase`; ответственный — составной FK на `project_collaborators` с `ON DELETE CASCADE`; CHECK `offset_hours BETWEEN 1 AND 8760` |
| `notifications`, `push_subscriptions` | CHECK «тип ⇔ заполненные поля»; триггер «статус меняется только из `PENDING`»; частичные UNIQUE ожидающих напоминаний; UNIQUE `endpoint` |
| `audit_logs` | без FK на `entity_id` и `actor_id`; CHECK `entity_type = split_part(action, '_', 1)` |

### Состояния и переходы

| Объект | Состояния | Переходы | Где защищено |
|---|---|---|---|
| Пользователь | `active`, `deleted` (`is_active`) | регистрация → `active`; `SoftDeleteUser`: `active → deleted` (идемпотентно, все сессии отзываются; 409, пока владеет активными проектами); `RestoreUser`: `deleted → active` (409, если username уже занят) | методы `User`; частичные уникальные индексы `WHERE is_active`; CHECK нижнего регистра username |
| Проект | `active`, `deleted` (`is_active`) | создание → `active`; `DeleteProject`: `active → deleted` только владельцем (403), обратного перехода в API нет; удалённый проект для всех операций — 404 | `Project.soft_delete(by=...)`; репозиторий не возвращает неактивный проект; отмена ожидающих уведомлений в той же транзакции |
| Коробка | `PACKING → PACKED → LOADED → DELIVERED → UNPACKED` | шаг вперёд или назад (`BOX_TRANSITIONS`); повтор текущего статуса — no-op; откат пишется в журнал отдельно | `Box.change_status`; в БД — CHECK значений и триггер `ck_boxes_status_transition`; гонки — условный `UPDATE … WHERE status = :expected` в репозитории |
| Вещь | `TO_PACK`, `IN_BOX`, `TAKEN_OUT`, `LOST` | матрица `ITEM_TRANSITIONS` (db-design, 5.2); `box_id` только у `IN_BOX` | методы `Item`; в БД — CHECK `(state = 'IN_BOX') = (box_id IS NOT NULL)` и триггер `ck_items_state_transition` |
| Задача | `open`, `done` (`done_at`) | `complete` / `reopen` идемпотентны; очистка срока удаляет напоминания | методы `Task`; в БД — CHECK `is_done = (done_at IS NOT NULL)` |
| Уведомление | `PENDING` → `SENT`, `FAILED`, `CANCELLED`, `SKIPPED` | только из `PENDING`, один раз. Воркер: причины нет → `CANCELLED`; доставлено → `SENT`; некуда доставить → `SKIPPED`; ошибка → повтор через 1, 2, 4, 8 мин, после 5-й попытки `FAILED` | методы `Notification` (`cancel`, `mark_sent`, `mark_skipped`, `record_failure`); в БД — CHECK по типу, триггер `ck_notifications_status_transition` и частичные UNIQUE; строка заблокирована на время отправки (`SKIP LOCKED`) |
| Push-подписка | `active`, `inactive` (`is_active`) | `PUT /push/subscriptions` → `active` (новый `endpoint` создаётся, известный переходит к вызывающему и оживает); `DELETE` владельцем, ответ 404/410 или непригодные ключи → `inactive` | `PushSubscription.subscribe` / `renew` / `deactivate`; UNIQUE `endpoint`, upsert `ON CONFLICT (endpoint)` при гонке |
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

1. слои `main | worker → ioc → config → presentation → infrastructure → application → domain`;
2. `domain` и `application` не импортируют сторонние пакеты: fastapi, sqlalchemy, pydantic,
   redis, structlog, prometheus_client, dishka, aiohttp, pywebpush, taskiq…;
3. доменные модули независимы друг от друга;
4. фичи application независимы, кроме `auth → users`;
5. presentation не обращается к `infrastructure.db/redis/security` напрямую, только через порты.

[tests/unit/test_architecture.py](../tests/unit/test_architecture.py) проверяет то, что
import-linter выразить не может:

- явные списки разрешённых импортов для каждого доменного модуля и фичи;
- «дом» каждой технологии: `sqlalchemy` только в `infrastructure/db` и `ioc/db_provider.py`,
  `redis` только в `infrastructure/redis` и `infrastructure/tasks`, `pywebpush`, `py_vapid`,
  `aiohttp` и `cryptography` только в `infrastructure/push`, `taskiq` только в `worker/` и
  `infrastructure/tasks`, `taskiq_redis` только в `infrastructure/tasks`, и т.д.;
- ORM-модели только в `infrastructure/db/models/`;
- у каждого use case есть `async def __call__` (модули `dto`, `services`, `lookup` и
  `read_model` фичи — не use cases);
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

- Тестовые аккаунты в проде (ADR 0018) — настоящие учётные записи: выключение флага их не удаляет,
  а смена `SEED_TEST_PASSWORD` не меняет пароль уже созданных.
- Пока Redis недоступен, каждый запрос ждёт таймаут Redis (200 мс, плюс один повтор) на
  каждую операцию. Rate limit и идемпотентность в это время не действуют. Улучшение —
  circuit breaker поверх `REDIS_FAILURES`.
- Fixed window пропускает до 2× лимита на стыке окон.
- HTTPS зависит от DNS и открытого `:80`. Пока Let's Encrypt не выдал сертификат (первый деплой,
  неверная DNS-запись), nginx отдаёт self-signed заглушку, и клиенты видят ошибку сертификата.
  Новый сертификат nginx подхватывает с задержкой до минуты. Сертификат выпускается на один домен.
- Ключи идемпотентности хранятся в Redis: после потери данных Redis повтор может создать дубль.
- Два параллельных refresh одним токеном: второй считается повтором и завершает сессию
  (семью). Так строже; смягчить можно коротким grace-периодом для только что отозванного токена.
- Перехэширование происходит только при входе: пароли пользователей, которые давно не
  входили, остаются со старыми параметрами argon2.
- Маппер заново проверяет значения из БД через Value Objects. Если правила ужесточить
  (например, формат username), уже сохранённая строка, которая им не отвечает, перестанет
  читаться. Ужесточение правил сопровождайте миграцией или скриптом, который находит и
  исправляет такие данные.
- Участие удалённого пользователя в проектах не удаляется, а скрывается из списка участников:
  так `RestoreUser` возвращает его. Пока аккаунт удалён, он по-прежнему занимает строку в
  `project_collaborators`, и напоминания для него ставятся в очередь; воркер отменяет их в
  момент отправки (`CANCELLED`). После восстановления аккаунта напоминания, срок которых
  пришёлся на время удаления, не возвращаются.
- Уведомления доставляются «хотя бы один раз»: если воркер упадёт после того, как push-сервис
  принял сообщение, но до коммита, уведомление придёт повторно. Задержка — до 15 с после
  `scheduled_at`.
- Доставка зависит от Redis: пока он недоступен, сигналы воркеру не доходят и уведомления
  задерживаются (не теряются, уйдут после возвращения Redis). Видно по `redis: degraded`, через
  5 минут — `notifications: degraded`, и по unhealthy `worker`.
- Планировщик должен быть один. Второй `scheduler` не ломает доставку, но удваивает пустые
  запуски. Пока `scheduler` не работает, уведомления стоят (в проде его перезапускает
  `restart: unless-stopped`). Своего healthcheck у него нет: остановку видно по heartbeat
  `worker` и проверке `notifications`.
- Один воркер отправляет последовательно: медленный push-сервис (таймаут 10 с на подписку)
  задерживает очередь. Масштабирование — ещё экземпляры `worker` (`SKIP LOCKED`).
- Ключ VAPID живёт в томе `vapid`. Потеря тома ломает все подписки браузеров (push-сервисы
  отвечают 403); ротации ключа нет, кроме «новый ключ и повторная подписка клиентов».
- У воркера нет `/metrics`: очередь видна по проверке `notifications` в `/health/ready`,
  healthcheck контейнера и логам.
- `vector` монтирует сокет Docker, а это фактически root на хосте. При его перезапуске
  (каждый деплой) строки за несколько секунд в Sage не попадают, а поле, сменившее тип в
  пределах одного `system`, уводит событие в dead-letters ([ADR 0017](adr/0017-logs-in-sage.md)).
  Логи Postgres в Sage не отправляются.
- Эндпоинтов задач пока нет, хотя домен и таблицы для них готовы. Сводка
  (`summary`) уже считает задачи и отдаёт `next_task`, но на стенде задач нет, пока их не
  создаёт API.
- Явные `operationId` есть только у маршрутов проектов и push; у `auth` и `users` они сгенерированы
  FastAPI, и SDK фронтенда получит для них длинные имена функций.
- Сканирование по фото зависит от внешнего прокси и его квот (240 запросов в минуту на
  аккаунт, лимит токенов). Ответ модели не детерминирован: названия могут отличаться между
  сканами одного фото, мелкие или перекрытые вещи пропускаются. Поэтому результат — только
  подсказка, пользователь проверяет его перед `createItems`. Фото и ответы не сохраняются,
  повторить скан без нового вызова модели нельзя. Резервного провайдера в коде нет: прокси
  сам балансирует, если указать модель без провайдера (`LLM__SCAN_MODEL`).
- Пока скан ждёт модель (до 30 с), запрос держит воркер uvicorn (не соединение с БД: его
  use case отдаёт до вызова). Лимит `scan` (10/мин с IP) ограничивает расход, но не общее
  число одновременных сканов.
- Миграция [0002](../migrations/versions/0002_drop_user_email.py) удаляет колонку `users.email`
  за один релиз, без expand/contract: код предыдущего релиза пишет в неё и на новой схеме не
  работает. Допустимо, пока шаблон не выкатывался на прод; иначе делите на два релиза (сначала
  `email` nullable, затем удаление). `downgrade()` возвращает колонку с заглушками
  `<id>@removed.invalid`: настоящие адреса не восстанавливаются.
- Удаление комнаты проверяет, что на неё никто не ссылается, без блокировки: коробка или вещь,
  созданная параллельно, может сослаться на комнату, которую в тот же момент удаляют. Данные не
  ломаются (внешний ключ на мягко удалённую комнату цел), но комната перестаёт быть видна в
  списке, пока на неё ссылаются. Улучшение — `SELECT … FOR UPDATE` строки комнаты в обоих
  сценариях.
- Курсор списков — ключ последней строки (номер коробки; приоритет, имя и id вещи). Если строку
  после курсора переименовали или перенумеровали, она может попасть на страницу дважды или
  пропасть: для ленты, которую листают за секунды, это допустимо.
