# API-контракт

Живая спецификация: `GET /docs` (Swagger UI) и `GET /openapi.json`. Ниже описаны соглашения,
которых не видно из схемы.

## Версионирование

- Бизнес-эндпоинты находятся под `/api/v1`. Несовместимое изменение выходит в `/api/v2`,
  а `v1` поддерживается до миграции клиентов.
- В `v1` допустимы совместимые изменения: новое необязательное поле запроса или новое поле
  ответа. Клиенты обязаны игнорировать неизвестные поля.
- Служебные эндпоинты не версионируются: `/health/live`, `/health/ready`, `/metrics`.

## Эндпоинты

| Метод | Путь | Успех | Основные ошибки |
|---|---|---|---|
| POST | `/api/v1/auth/register` `{username, password}` | 201 `UserResponse` | 409 `username_taken`, 422 |
| POST | `/api/v1/auth/login` `{username, password}` | 200 `TokenPairResponse` | 401 `invalid_credentials` |
| POST | `/api/v1/auth/refresh` `{refresh_token}` | 200 `TokenPairResponse` | 401 `invalid_refresh_token` |
| POST | `/api/v1/auth/logout` `{refresh_token}` | 204, сессия завершена | — (идемпотентно) |
| GET | `/api/v1/users/me` | 200 `UserResponse` | 401 |
| PUT | `/api/v1/users/me/password` `{current_password, new_password}` | 204, все сессии завершены | 401 `invalid_credentials`, 422 |
| PUT | `/api/v1/users/me/username` `{new_username}` | 200 `UserResponse` | 409 `username_taken`, 422 |
| DELETE | `/api/v1/users/me` | 204, мягкое удаление | 401, 409 `user_owns_projects` |
| GET | `/api/v1/projects` (`listProjects`) | 200 `ProjectResponse[]` по `moving_date` | 401 |
| POST | `/api/v1/projects` (`createProject`) `{name, moving_date, time_zone, …}` | 201 `ProjectResponse` | 409 `room_name_taken`, 422 `invalid_time_zone` и др. |
| GET | `/api/v1/projects/{id}` (`getProject`) | 200 `ProjectResponse` | 404 `project_not_found` |
| PATCH | `/api/v1/projects/{id}` (`updateProject`) | 200 `ProjectResponse` | 404 `project_not_found`, 422 |
| DELETE | `/api/v1/projects/{id}` (`deleteProject`) | 204, мягкое удаление | 403 `not_project_owner`, 404 `project_not_found` |
| PUT | `/api/v1/projects/{id}/reminders` (`setProjectReminders`) `{offsets_days}` | 200 `ProjectResponse` | 404 `project_not_found`, 422 `invalid_moving_reminder_offset` |
| GET | `/api/v1/projects/{id}/collaborators` (`listCollaborators`) | 200 `CollaboratorResponse[]`, владелец первым | 404 `project_not_found` |
| POST | `/api/v1/projects/{id}/collaborators` (`addCollaborator`) `{username}` | 201 `CollaboratorResponse`, 200 если уже участник | 404 `project_not_found` / `user_not_found` |
| DELETE | `/api/v1/projects/{id}/collaborators/{user_id}` (`removeCollaborator`) | 204 | 404 `project_not_found`, 409 `cannot_remove_owner` |
| GET | `/api/v1/push/vapid-public-key` (`getVapidPublicKey`) | 200 `{public_key}` | — (без аутентификации) |
| PUT | `/api/v1/push/subscriptions` (`upsertPushSubscription`) `{endpoint, keys: {p256dh, auth}}` | 204, upsert по `endpoint` | 401, 422 `invalid_push_subscription` |
| DELETE | `/api/v1/push/subscriptions?endpoint=` (`deletePushSubscription`) | 204 всегда | 401 |
| GET | `/health/live` | 200 `{"status":"ok"}` | — |
| GET | `/health/ready` | 200 `ok`/`degraded` | 503 `unavailable` |

На всех `/api/v1/*` возможны 429 `rate_limited` и 500 `internal_error`. Эндпоинты `users`,
`projects` и `push/subscriptions` требуют `Authorization: Bearer <access_token>`. Явный
`operationId` (в скобках) пока есть только у маршрутов проектов и push. Если api недоступен, nginx отвечает 503 `service_unavailable` в том же формате и с `Retry-After`.

### Инвентарь: комнаты, коробки, вещи

Контракт по [api-endpoints.md](../api-endpoints.md) (разделы 5–7, 9). Все операции требуют
`Authorization: Bearer`, кроме `getBoxByQr`, где токен необязателен. Объекту чужого проекта
отвечает 404, а не 403: для не-участника проекта и его содержимого не существует. У каждой
операции явный `operationId`, его проверяет `tests/unit/presentation/test_openapi.py`.
`PATCH` меняет только переданные поля; `null` очищает необязательное поле (`title` коробки),
а у остальных полей даёт 422 `validation_error`.

| Метод | Путь (`/api/v1`) | operationId | Успех | Ошибки |
|---|---|---|---|---|
| GET | `/projects/{project_id}/rooms?kind=` | `listRooms` | 200 `Room[]` | 404 `project_not_found` |
| POST | `/projects/{project_id}/rooms` `{kind, name}` | `createRoom` | 201 `Room` | 404 `project_not_found`, 409 `room_name_taken` |
| PATCH | `/rooms/{room_id}` `{name}` | `updateRoom` | 200 `Room` | 404 `room_not_found`, 409 `room_name_taken` |
| DELETE | `/rooms/{room_id}` | `deleteRoom` | 204 | 404 `room_not_found`, 409 `room_in_use` |
| GET | `/projects/{project_id}/boxes?status=&destination_room_id=&number=&limit=&after=` | `listBoxes` | 200 `BoxPage` | 404 `project_not_found` |
| POST | `/projects/{project_id}/boxes` | `createBox` | 201 `Box` | 404 `project_not_found`/`room_not_found`, 422 `wrong_room_kind` |
| GET | `/boxes/by-qr/{qr_token}` | `getBoxByQr` | 200 `BoxByQr` | 401 `not_authenticated` (`PROTECTED` без входа), 404 `box_not_found` |
| GET | `/boxes/{box_id}` | `getBox` | 200 `Box` | 404 `box_not_found` |
| PATCH | `/boxes/{box_id}` `{title, is_fragile, destination_room_id}` | `updateBox` | 200 `Box` | 404 `box_not_found`/`room_not_found`, 422 `wrong_room_kind` |
| PUT | `/boxes/{box_id}/status` `{status}` | `setBoxStatus` | 200 `Box` | 404 `box_not_found`, 409 `invalid_box_transition`/`box_status_conflict` |
| DELETE | `/boxes/{box_id}` | `deleteBox` | 204 | 404 `box_not_found` |
| PUT | `/boxes/{box_id}/contents-reminder` `{remind_at}` | `setBoxContentsReminder` | 200 `Box` | 403 `not_box_creator`, 404 `box_not_found`, 409 `box_not_packing`, 422 `reminder_in_the_past` |
| DELETE | `/boxes/{box_id}/contents-reminder` | `cancelBoxContentsReminder` | 204 | 403 `not_box_creator`, 404 `box_not_found` |
| GET | `/projects/{project_id}/items?state=&priority=&box_id=&source_room_id=&limit=&after=` | `listItems` | 200 `ItemPage` | 404 `project_not_found` |
| POST | `/projects/{project_id}/items` | `createItem` | 201 `Item` | 404 `project_not_found`/`room_not_found`/`box_not_found`, 422 `wrong_room_kind` |
| POST | `/projects/{project_id}/items/batch` (до 100 записей) | `createItems` | 201 `Item[]` | 404 `project_not_found`/`room_not_found`/`box_not_found`, 422 |
| GET | `/projects/{project_id}/items/search?q=&limit=&offset=` | `searchItems` | 200 `ItemSearchResult` | 404 `project_not_found` |
| GET | `/items/{item_id}` | `getItem` | 200 `Item` | 404 `item_not_found` |
| PATCH | `/items/{item_id}` `{name, quantity, priority, source_room_id}` | `updateItem` | 200 `Item` | 404 `item_not_found`/`room_not_found`, 422 `wrong_room_kind` |
| PUT | `/items/{item_id}/state` `{state, box_id?}` | `setItemState` | 200 `Item` | 404 `item_not_found`/`box_not_found`, 409 `invalid_item_transition`, 422 `item_box_mismatch` |
| DELETE | `/items/{item_id}` | `deleteItem` | 204 | 404 `item_not_found` |
| GET | `/items/{item_id}/suggestions` | `getItemSuggestions` | 200 `ItemSuggestion[]` | 404 `item_not_found` |

Длинные списки отдаются по курсору: `{"results": [...], "next_cursor": "..." | null}`, `limit`
от 1 до 100 (по умолчанию 50). Поиск сортирует по рангу и поэтому пагинируется через
`limit`/`offset`: `{"results": [...], "has_more": true}`. Если api недоступен, nginx отвечает

### Сводки для экранов

Только чтение: обзор, прогресс и этикетки по всему проекту (раздел 10
[api-endpoints.md](../api-endpoints.md)). Списки коробок и вещей постраничные, поэтому
счётчики считает сервер. Доступ — любому участнику, иначе 404 `project_not_found`. Решения —
в [ADR 0012](adr/0012-summaries-read-model.md).

| Метод | Путь (`/api/v1`) | operationId | Успех | Ошибки |
|---|---|---|---|---|
| GET | `/projects/{project_id}/summary` | `getProjectSummary` | 200 `ProjectSummary` | 404 `project_not_found` |
| GET | `/projects/{project_id}/loading-checklist` | `getLoadingChecklist` | 200 `LoadingChecklistRoom[]` | 404 `project_not_found` |
| GET | `/projects/{project_id}/unpacking-progress` | `getUnpackingProgress` | 200 `UnpackingProgressRoom[]` | 404 `project_not_found` |
| GET | `/projects/{project_id}/labels?box_ids=` | `getBoxLabels` | 200 `BoxLabel[]` | 404 `project_not_found`, 422 `validation_error` (больше 100 `box_ids`) |

- **`summary`**:
  - `boxes {total, by_status}`, `items {total, by_state}`,
    `tasks {total, done, by_phase: {BEFORE|DURING|AFTER: {total, done}}}`. В `by_*` есть поле
    для каждого значения enum, нули включены. Считаются только активные (не удалённые)
    объекты, и все счётчики берутся из одного снимка БД.
  - `first_necessity {total, taken_out}` — чек-лист первого дня: вещи `FIRST_NECESSITY` и
    сколько из них уже `TAKEN_OUT`.
  - `next_task` — первая невыполненная задача (`Task`) по `phase` (`BEFORE` → `DURING` →
    `AFTER`), затем `due_at` (без срока — последними), затем по времени создания; `null`, если
    всё сделано. В `assignees` нет удалённых аккаунтов.
- **`loading-checklist`** — все активные комнаты «откуда» по имени, включая пустые: `room`,
  `to_pack_count` (вещи `TO_PACK` этой комнаты), `boxes` (`BoxRef` коробок с её вещами
  `IN_BOX`, по номеру), `is_loaded`. Комната погружена, если в ней нечего паковать и все её
  коробки в `LOADED` или дальше. Комната без вещей тоже считается погруженной. Вещи
  `TAKEN_OUT` и `LOST` не учитываются.
- **`unpacking-progress`** — все активные комнаты «куда» по имени, включая пустые: `room`,
  `total` (активные коробки в эту комнату), `unpacked` (из них `UNPACKED`).
- **`labels`** — `BoxLabel[]` по `number`: `id`, `number`, `title`, `is_fragile`,
  `destination_room`, `qr_token`, `item_names` (по приоритету, затем по имени). Без `box_ids`
  возвращаются все активные коробки, `box_ids` повторяется для выбранных
  (`?box_ids=…&box_ids=…`). Чужие, удалённые и неизвестные id пропускаются, ошибки нет.

## Формат ошибок

Любой ответ с кодом не 2xx имеет одну форму, включая ошибки валидации, 500 и ответы самого nginx:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed",
    "details": [{"field": "body.username", "message": "Field required"}],
    "request_id": "f111fa523be247119f431b23fd679298"
  }
}
```

- `code` стабилен, клиент ветвится по нему. `message` предназначен людям и может меняться.
- `details` всегда присутствует (может быть пустым). У ошибок валидации поле `field`
  имеет вид `body.username`, `query.limit`, `header.idempotency-key`.
- `request_id` совпадает с заголовком `X-Request-ID` и с полем `request_id` в логах api и nginx.
- 500 никогда не раскрывает внутренности: только `internal_error` и `request_id`.
- Все 401 содержат `WWW-Authenticate: Bearer`.

| HTTP | `code` | Когда |
|---|---|---|
| 401 | `not_authenticated` | нет токена, токен невалиден или истёк, пользователь удалён |
| 401 | `invalid_credentials` | неверное имя пользователя или пароль (неотличимы намеренно) |
| 401 | `invalid_refresh_token` | refresh-токен неизвестен, истёк, отозван или использован повторно |
| 403 | `not_project_owner` | действие доступно только владельцу проекта (удаление проекта) |
| 404 | `user_not_found` | нет пользователя, в том числе активного с таким `username` при добавлении участника |
| 404 | `project_not_found` | проекта нет, он удалён или пользователь не участник (неотличимы намеренно) |
| 403 | `not_box_creator` | напоминание о содержимом ставит или отменяет не создатель коробки |
| 404 | `room_not_found`, `box_not_found`, `item_not_found` | объекта нет, он удалён или вызывающий не участник проекта |
| 404 / 405 | `not_found`, `method_not_allowed` | нет маршрута или метода |
| 409 | `username_taken` | имя пользователя занято активным пользователем |
| 409 | `request_in_progress` | запрос с тем же `Idempotency-Key` ещё выполняется |
| 409 | `cannot_remove_owner` | попытка убрать владельца из участников проекта |
| 409 | `user_owns_projects` | удаление аккаунта, который владеет активными проектами: сначала удалите их |
| 409 | `room_name_taken` | две комнаты одного вида с одним именем в проекте |
| 409 | `invalid_box_transition` | статус коробки меняется не на один шаг вперёд или назад |
| 409 | `invalid_item_transition` | переход вещи вне матрицы состояний (`docs/db-design.md`, 5.2) |
| 409 | `reminder_without_due_date` | напоминание у задачи без срока |
| 409 | `room_in_use` | на комнату ссылаются активные коробки или вещи |
| 409 | `box_status_conflict` | статус коробки изменили параллельно: перечитайте её |
| 409 | `box_not_packing` | напоминание о содержимом для коробки не в `PACKING` |
| 422 | `validation_error` | тело, query или заголовок не соответствуют схеме |
| 422 | `invalid_username`, `invalid_password`, `invalid_value` | значение нарушает правило домена |
| 422 | `invalid_project_name`, `invalid_address`, `invalid_time_zone`, `invalid_room_name`, `invalid_box_number`, `invalid_box_title`, `invalid_qr_token`, `invalid_item_name`, `invalid_quantity`, `invalid_task_title`, `invalid_task_description`, `invalid_due_date`, `invalid_reminder_offset`, `invalid_moving_reminder_offset`, `invalid_push_subscription` | значение нарушает правило домена (длина, диапазон, формат; срок без часового пояса; неизвестный IANA-пояс) |
| 422 | `wrong_room_kind` | коробке нужна комната «куда», вещи — комната «откуда» |
| 422 | `cross_project_reference` | ссылка на комнату или коробку другого проекта |
| 422 | `item_box_mismatch` | `box_id` не передан для `IN_BOX` или передан для другого состояния |
| 422 | `reminder_in_the_past` | время напоминания уже прошло |
| 422 | `invalid_pagination` | `limit` вне 1..100 или `offset < 0` |
| 422 | `idempotency_key_reused` | ключ уже использован с другим телом запроса |
| 429 | `rate_limited` | превышен лимит, см. `Retry-After` |
| 500 | `internal_error` | непредвиденная ошибка; ищите `request_id` в логах |
| 503 | `service_unavailable` | отвечает nginx: api недоступен |

## Валидация

Проверка идёт в два этапа:

1. **Pydantic** проверяет форму запроса: типы, обязательные поля, отсутствие лишних полей
   (`extra="forbid"`). Ошибка → `validation_error`.
2. **Value Objects домена** проверяют бизнес-правила: формат и длину username,
   длину пароля. Ошибка → код конкретного правила.

Правила не дублируются в схемах, и ни один путь в обход API (например, CLI) их не минует.

Нормализация выполняется в домене: username хранится в нижнем регистре без пробелов по
краям, поэтому `John_Doe` и `john_doe` — одно имя. Символ `@` в username недопустим. Пароль не
обрезается: пробелы — его законная часть.

## Пагинация

Списочные эндпоинты отвечают `{"items": [...], "total": 57, "limit": 20, "offset": 0}`
(`PageResponse`). `limit` — 1..100, по умолчанию 20; иначе 422 `invalid_pagination`. Сейчас
его не использует ни один эндпоинт: механизм (`PageRequest`, `PageResponse`) ждёт длинных
списков (коробки, вещи). Короткие списки (проекты, участники) отдаются целиком массивом.

## Идемпотентность (`Idempotency-Key`)

Механизм для небезопасных эндпоинтов, которые клиент может повторить после обрыва связи.
Сейчас его использует `POST /api/v1/projects` (scope `create_project:<user_id>`); новый
эндпоинт подключает его через `run_idempotent(...)` и параметр `IdempotencyKey`. Заголовок необязателен, длина 8–128
символов. Ключ привязывается к операции и пользователю через `scope` (например,
`create_project:<user_id>`): одинаковые ключи разных пользователей не конфликтуют.

| Ситуация | Ответ |
|---|---|
| ключ новый | запрос выполняется, результат сохраняется на 24 ч |
| тот же ключ и то же тело после успеха | исходный ответ и `Idempotent-Replayed: true`; операция не повторяется |
| тот же ключ, первый запрос ещё выполняется | 409 `request_in_progress`; повторите позже |
| тот же ключ, другое тело | 422 `idempotency_key_reused` |
| первый запрос завершился ошибкой | ключ освобождается, повтор выполнится заново |
| Redis недоступен | запрос выполняется без защиты от повторов (fail-open) |

Состояние `pending` живёт 30 секунд: упавший процесс api не блокирует ключ на сутки.

## Проекты

Решения и альтернативы — в [ADR 0010](adr/0010-projects-api.md).

- **Доступ.** Все операции доступны только участникам. Не-участник получает 404
  `project_not_found`, как и для несуществующего или удалённого проекта: существование чужого
  проекта не раскрывается. 403 `not_project_owner` — только участнику, который удаляет чужой
  проект. Остальное (правка, напоминания, участники) разрешено любому участнику.
- **`POST /projects`.** Обязательны `name`, `moving_date`, `time_zone` (IANA, например
  `Europe/Moscow`). Необязательны `origin_address`, `destination_address`, `qr_code_strategy`
  (по умолчанию `PROTECTED`), `reminder_offsets_days` и `rooms: {source, destination}` —
  комнаты создаются в той же транзакции. Поддерживает `Idempotency-Key`.
- **`PATCH /projects/{id}`.** Неотправленное поле не меняется. `null` очищает адрес; `null`
  в `name`, `moving_date`, `time_zone`, `qr_code_strategy` — 422 `validation_error`. Смена
  `moving_date` или `time_zone` переносит ожидающие напоминания о переезде.
- **`DELETE /projects/{id}`.** Только владелец. Мягкое удаление, для пользователей
  окончательное; ожидающие уведомления проекта отменяются.
- **Напоминания о переезде** (`PUT /projects/{id}/reminders {offsets_days}`). Набор заменяется
  целиком, повтор того же набора ничего не меняет. Смещение — 0..365 дней, 0 — день переезда.
  Уведомление получает каждый участник в 09:00 по `time_zone` проекта в день
  `moving_date − offset`; уже прошедшие моменты не ставятся. В ответе
  `reminder_offsets_days` по возрастанию.
- **Участники.** `POST …/collaborators {username}`: 201 — добавлен, 200 — уже был участником
  (ничего не изменилось). `DELETE …/collaborators/{user_id}`: убрать может любой участник,
  свой `user_id` — выход из проекта; владельца убрать нельзя (409), не-участник — no-op (204).
  Назначения на задачи уходят вместе с участием, ожидающие уведомления в проекте отменяются.
  Удалённые аккаунты в списке участников не показываются.
- **Ответы.** `ProjectResponse`: `id`, `name`, `moving_date`, `origin_address`,
  `destination_address`, `qr_code_strategy`, `time_zone`, `reminder_offsets_days`,
  `owner {id, username}`, `is_owner` (текущий пользователь — владелец), `created_at`,
  `updated_at`. `CollaboratorResponse`: `user {id, username}`, `is_owner`, `created_at`
  (время вступления).
- **Удаление аккаунта.** Владелец активных проектов получает 409 `user_owns_projects`.

## Web Push

Уведомления (назначение на задачу, срок задачи, опись коробки, дата переезда) отправляет
воркер ([ADR 0011](adr/0011-notification-worker.md)), API только регистрирует браузеры.

- **`GET /push/vapid-public-key`** → `{"public_key": "..."}`: `applicationServerKey` для
  `pushManager.subscribe()` (base64url, P-256). Без аутентификации, ключ стабилен.
- **`PUT /push/subscriptions`.** Тело — `PushSubscription.toJSON()` браузера как есть:
  `{endpoint, expirationTime, keys: {p256dh, auth}}`; `expirationTime` игнорируется. Ответ 204.
  Upsert по `endpoint`: повтор ничего не ломает, браузер, подписанный под другим аккаунтом,
  переходит к текущему пользователю, отключённая подписка оживает с новыми ключами.
  `endpoint` — https-URL до 2048 символов, ключи — base64url; иначе 422
  `invalid_push_subscription`.
- **`DELETE /push/subscriptions?endpoint=...`** → всегда 204. Отключает подписку, только
  если она принадлежит вызывающему: ответ не раскрывает, чей это браузер.
- **Клиенту.** Вызывайте `PUT` после `subscribe()` и при каждом запуске приложения
  (браузер мог сменить подписку), `DELETE` — при выходе из аккаунта. Подписку, на которую
  push-сервис ответил 404/410, сервер отключает сам.
- **Payload** (JSON, читает service worker): `title`, `body`, `url` (путь фронтенда: `/plan`,
  `/b/{qr_token}` или `/dashboard`), `type` (`TASK_ASSIGNED`, `TASK_REMINDER`,
  `BOX_CONTENTS_REMINDER`, `MOVING_DATE_REMINDER`), `project_id`, `notification_id`, а также
  `task_id` или `box_id`. Тексты на русском, содержимое коробок в них не попадает; срок задачи
  показан в часовом поясе проекта.
- Уведомления пользователю без активных подписок не копятся: они получают статус `SKIPPED`.

## Аутентификация

- `POST /auth/login` открывает новую сессию и возвращает `access_token` (JWT HS256,
  15 минут) и `refresh_token` (случайная строка, 30 дней), а также `expires_in` и
  `refresh_token_expires_in` в секундах. Если хэш пароля сделан с устаревшими параметрами
  argon2, он пересчитывается при этом входе; для клиента это незаметно.
- `POST /auth/refresh` выдаёт новую пару в той же сессии, старый refresh-токен перестаёт
  работать. Повторное предъявление уже использованного токена считается кражей: завершается
  **эта сессия** (вся семья токенов). Другие сессии пользователя (устройства) продолжают
  работать.
- `POST /auth/logout` завершает сессию, которой принадлежит refresh-токен, даже если клиент
  прислал уже ротированный токен. Access-токен действует до истечения срока.
- Смена пароля и удаление аккаунта завершают все сессии. Удалённый пользователь получает 401
  на первом же запросе: пользователь читается из БД при каждом запросе.

## Rate limiting

Используется фиксированное окно в 60 секунд, счётчик ведётся по IP клиента. IP берётся из
`X-Forwarded-For`, который nginx перезаписывает, поэтому подменить его нельзя.

| Scope | По умолчанию | Где |
|---|---|---|
| `api` | 120/мин (`RATE_LIMIT__API_PER_MINUTE`) | все `/api/v1/*` |
| `auth` | 10/мин (`RATE_LIMIT__AUTH_PER_MINUTE`) | `register`, `login`, `refresh`, дополнительно к `api` |

Ответы содержат `X-RateLimit-Limit` и `X-RateLimit-Remaining`, ответ 429 ещё и `Retry-After`.
Лимиты не применяются к `/health/*` и `/metrics`. Значение `0` отключает scope. Если Redis
недоступен, лимит не действует (fail-open).

## Заголовки

| Заголовок | Направление | Смысл |
|---|---|---|
| `X-Request-ID` | запрос и ответ | сквозной идентификатор; принимается, если совпадает с `[A-Za-z0-9._-]{1,64}`, иначе генерируется |
| `Idempotency-Key` | запрос | см. выше |
| `Idempotent-Replayed: true` | ответ | ответ взят из хранилища идемпотентности |
| `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `Retry-After` | ответ | rate limit |
| `WWW-Authenticate: Bearer` | ответ 401 | схема аутентификации |
