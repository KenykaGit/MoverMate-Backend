# MoverMate: HTTP API для фронтенда

Список эндпоинтов, которые нужны фронтенду, с учётом решений из [frontend-backend-alignment.md](frontend-backend-alignment.md) (раздел 4).
Модель данных — [db-design.md](db-design.md) и [ADR 0009](0009-moving-domain-aggregates.md) плюс принятые изменения: этап задачи `phase`, `title` и `is_fragile` у коробки, ручной выбор шаблонов, напоминание о дате переезда, удаление проекта только владельцем.

---

## 1. Общие соглашения

### 1.1. Адрес и спецификация

- Все пути указаны относительно `NEXT_PUBLIC_API_URL`.
- Спецификация OpenAPI доступна по `{NEXT_PUBLIC_API_URL}/openapi.json`, из неё фронтенд генерирует SDK (`npm run openapi`).
- `operationId` задаётся явно (в FastAPI — через `generate_unique_id_function` или `operation_id=`) и совпадает с колонкой `operationId` в таблицах ниже. От него зависят имена функций SDK, без этого генератор выдаст `list_boxes_projects__project_id__boxes_get`.

### 1.2. Аутентификация

- `access_token` возвращается в теле ответа, клиент отправляет его в `Authorization: Bearer <token>`.
- Refresh-токен хранится в cookie `HttpOnly; Secure; SameSite=Lax; Path=/auth`. Если фронтенд и API на разных origin, нужны CORS с `allow_credentials=true` и явным origin фронтенда, а клиент шлёт запросы к `/auth/*` с `credentials: "include"`.
- Истёкший access-токен → 401 с кодом `AUTH_TOKEN_EXPIRED`, клиент вызывает `/auth/refresh` и повторяет запрос.

### 1.3. Доступ

| Обозначение | Кто |
|-------------|-----|
| **А** | любой, без входа |
| **В** | любой вошедший |
| **У** | участник проекта |
| **Вл** | владелец проекта |

Для пользователя, который не участвует в проекте, проект и всё его содержимое не существуют: ответ 404, а не 403. Ответ 403 бывает только у участника, который вызвал действие владельца. Исключение — чтение коробки по QR (раздел 9).

### 1.4. Форматы

- `id` — UUID (v7) в виде строки.
- Время — ISO 8601 в UTC (`2026-11-03T09:00:00Z`), дата переезда — `YYYY-MM-DD`.
- Перечисления — строки в верхнем регистре, в том числе приоритет вещи (в БД он `smallint`).
- `PATCH` — частичное обновление: переданные поля меняются, отсутствующие остаются как есть, `null` очищает необязательное поле.

### 1.5. Пагинация

Длинные списки (коробки, вещи) отдаются по курсору:

```
GET ...?limit=50&after=<cursor>
```

```json
{ "results": [ ... ], "next_cursor": "eyJuIjo0Mn0" }
```

- `limit` от 1 до 100, по умолчанию 50.
- `next_cursor` — непрозрачная строка, `null` на последней странице.
- Короткие списки (проекты, комнаты, участники, задачи) отдаются целиком массивом.

### 1.6. Ошибки

Ошибки валидации (422) — в стандартном формате FastAPI. Остальные ошибки — в едином формате:

```json
{ "code": "BOX_STATUS_TRANSITION", "message": "Нельзя перейти из PACKING в LOADED", "details": { "current": "PACKING", "requested": "LOADED" } }
```

| HTTP | `code` | Когда |
|------|--------|-------|
| 401 | `AUTH_INVALID_CREDENTIALS` | неверные имя или пароль |
| 401 | `AUTH_TOKEN_EXPIRED` | истёк access-токен |
| 401 | `AUTH_REQUIRED` | нет токена там, где нужен вход (в том числе QR в режиме `PROTECTED`) |
| 403 | `NOT_PROJECT_OWNER` | действие владельца вызвал другой участник |
| 403 | `NOT_BOX_CREATOR` | напоминание о содержимом ставит не создатель коробки |
| 404 | `NOT_FOUND` | объекта нет, он удалён или пользователь не участник |
| 404 | `USER_NOT_FOUND` | добавление участника по несуществующему `username` |
| 409 | `USERNAME_TAKEN` | имя занято |
| 409 | `USER_OWNS_PROJECTS` | удаление аккаунта владельца активных проектов |
| 409 | `CANNOT_REMOVE_OWNER` | удаление владельца из участников |
| 409 | `ROOM_NAME_TAKEN` | комната с таким именем и видом уже есть |
| 409 | `ROOM_IN_USE` | удаление комнаты, на которую ссылаются коробки или вещи |
| 409 | `BOX_STATUS_TRANSITION` | переход статуса коробки вне матрицы (5.1) или гонка; в `details` — текущий статус |
| 409 | `BOX_NOT_PACKING` | напоминание о содержимом для коробки не в `PACKING` |
| 409 | `ITEM_STATE_TRANSITION` | переход состояния вещи вне матрицы (5.2); в `details` — текущее состояние |
| 422 | `ROOM_KIND_MISMATCH` | у коробки комната не `DESTINATION` или у вещи не `SOURCE` |
| 422 | `ASSIGNEE_NOT_COLLABORATOR` | ответственный не участник проекта |
| 422 | `REMINDER_REQUIRES_DUE_AT` | напоминания у задачи без срока |
| 422 | `UNKNOWN_TEMPLATE` | неизвестный `template_id` |

### 1.7. Идемпотентность

- Смена статуса коробки, состояния вещи, отметка задачи, замена ответственных и напоминаний — `PUT` или идемпотентный `POST`. Повтор с тем же значением возвращает 200 с актуальным объектом и ничего не пишет в журнал.
- Создание (`POST` новых объектов) не идемпотентно, клиент его автоматически не повторяет.

---

## 2. Схемы ответов

```ts
UserRef      = { id, username }
User         = { id, username, created_at }
RoomRef      = { id, name }
Room         = { id, kind: "SOURCE" | "DESTINATION", name }

Project = {
  id, name,
  moving_date: "YYYY-MM-DD",
  origin_address: string | null,
  destination_address: string | null,
  qr_code_strategy: "PROTECTED" | "OPEN",
  time_zone: string,                 // IANA, например "Europe/Moscow"
  reminder_offsets_days: number[],   // напоминания о дате переезда
  owner: UserRef,
  is_owner: boolean,                 // текущий пользователь — владелец
  created_at, updated_at,
}

Collaborator = { user: UserRef, is_owner: boolean, created_at }

BoxStatus = "PACKING" | "PACKED" | "LOADED" | "DELIVERED" | "UNPACKED"
BoxRef    = { id, number, status: BoxStatus, destination_room: RoomRef }

Box = {
  id, number,
  title: string | null,
  is_fragile: boolean,
  destination_room: RoomRef,
  status: BoxStatus,
  status_changed_at,
  created_by: UserRef,
  qr_token: string,
  item_count: number,                // активные вещи IN_BOX
  items_preview: string[],           // до 3 названий для карточки в списке
  contents_reminder_at: string | null, // только для создателя коробки, иначе null
  created_at, updated_at,
}

ItemState    = "TO_PACK" | "IN_BOX" | "TAKEN_OUT" | "LOST"
ItemPriority = "FIRST_NECESSITY" | "HIGH" | "MEDIUM" | "LOW"

Item = {
  id, name, quantity,
  priority: ItemPriority,
  state: ItemState,
  source_room: RoomRef,
  box: BoxRef | null,                // заполнен только при IN_BOX
  created_at, updated_at,
}

TaskPhase = "BEFORE" | "DURING" | "AFTER"

Task = {
  id, title,
  description: string | null,
  phase: TaskPhase,
  due_at: string | null,
  is_done: boolean,
  done_at: string | null,
  assignees: UserRef[],
  reminder_offsets_hours: number[],
  created_at, updated_at,
}

TaskTemplate = { id: string, title, description: string | null, phase: TaskPhase }

AuthTokens = { access_token, token_type: "bearer", expires_in: number, user: User }
```

---

## 3. Служебное и аккаунт

| Метод и путь | operationId | Доступ | Описание |
|--------------|-------------|--------|----------|
| `GET /health` | `health` | А | `SELECT 1`, ответ `{ "status": "ok" }` |
| `POST /auth/register` | `register` | А | `{ username, password }` → 201 `AuthTokens` + cookie |
| `POST /auth/login` | `login` | А | `{ username, password }` → 200 `AuthTokens` + cookie |
| `POST /auth/refresh` | `refreshToken` | cookie | → 200 `AuthTokens`, refresh-токен ротируется |
| `POST /auth/logout` | `logout` | В | → 204, refresh отзывается, cookie очищается |
| `GET /users/me` | `getMe` | В | → `User` |
| `DELETE /users/me` | `deleteMe` | В | → 204, все сессии отзываются; 409 `USER_OWNS_PROJECTS` |

`username`: 3–32 символа, бэкенд приводит к нижнему регистру. Ограничения на пароль бэкенд отдаёт в OpenAPI (`minLength`), чтобы фронтенд проверял то же самое.

---

## 4. Проекты и участники

| Метод и путь | operationId | Доступ | Описание |
|--------------|-------------|--------|----------|
| `GET /projects` | `listProjects` | В | активные проекты, где пользователь участник → `Project[]`, по `moving_date` |
| `POST /projects` | `createProject` | В | → 201 `Project`; создатель становится владельцем и участником |
| `GET /projects/{project_id}` | `getProject` | У | → `Project` |
| `PATCH /projects/{project_id}` | `updateProject` | У | → `Project` |
| `DELETE /projects/{project_id}` | `deleteProject` | Вл | → 204; 403 `NOT_PROJECT_OWNER` |
| `PUT /projects/{project_id}/reminders` | `setProjectReminders` | У | `{ offsets_days: number[] }` → `Project` |
| `GET /projects/{project_id}/collaborators` | `listCollaborators` | У | → `Collaborator[]`, владелец первым |
| `POST /projects/{project_id}/collaborators` | `addCollaborator` | У | `{ username }` → 201 `Collaborator` (200, если уже участник); 404 `USER_NOT_FOUND` |
| `DELETE /projects/{project_id}/collaborators/{user_id}` | `removeCollaborator` | У | → 204; 409 `CANNOT_REMOVE_OWNER`. Своим `user_id` участник выходит из проекта |

**`POST /projects`**

```json
{
  "name": "Переезд на Садовую",
  "moving_date": "2026-11-20",
  "origin_address": "ул. Ленина, 14",
  "destination_address": "ул. Садовая, 8",
  "qr_code_strategy": "PROTECTED",
  "time_zone": "Europe/Moscow",
  "reminder_offsets_days": [7, 1],
  "rooms": {
    "source": ["Кухня", "Спальня"],
    "destination": ["Кухня", "Спальня", "Детская"]
  }
}
```

- Необязательны `origin_address`, `destination_address`, `qr_code_strategy` (по умолчанию `PROTECTED`), `reminder_offsets_days` (по умолчанию `[]`) и `rooms`.
- Комнаты создаются в той же транзакции. Шаблоны задач автоматически не копируются.

**`PATCH /projects/{project_id}`**: поля `name`, `moving_date`, `origin_address`, `destination_address`, `qr_code_strategy`, `time_zone`. При смене `moving_date` или `time_zone` ожидающие напоминания о переезде пересчитываются.

**Напоминания о дате переезда:**
- `offsets_days` — целые числа от 0 до 365.
- Уведомление уходит всем участникам в 09:00 по `time_zone` проекта в день `moving_date − offset`.
- UI на фронтенде появится вместе с Web Push.

---

## 5. Комнаты

| Метод и путь | operationId | Доступ | Описание |
|--------------|-------------|--------|----------|
| `GET /projects/{project_id}/rooms?kind=` | `listRooms` | У | → `Room[]`, по имени; без `kind` — обе группы |
| `POST /projects/{project_id}/rooms` | `createRoom` | У | `{ kind, name }` → 201 `Room`; 409 `ROOM_NAME_TAKEN` |
| `PATCH /rooms/{room_id}` | `updateRoom` | У | `{ name }` → `Room`; `kind` не меняется |
| `DELETE /rooms/{room_id}` | `deleteRoom` | У | → 204; 409 `ROOM_IN_USE` |

---

## 6. Коробки

| Метод и путь | operationId | Доступ | Описание |
|--------------|-------------|--------|----------|
| `GET /projects/{project_id}/boxes` | `listBoxes` | У | → `Page<Box>`, по `number` |
| `POST /projects/{project_id}/boxes` | `createBox` | У | → 201 `Box`, статус `PACKING` |
| `GET /boxes/{box_id}` | `getBox` | У | → `Box` |
| `PATCH /boxes/{box_id}` | `updateBox` | У | `{ title, is_fragile, destination_room_id }` → `Box`; 422 `ROOM_KIND_MISMATCH` |
| `PUT /boxes/{box_id}/status` | `setBoxStatus` | У | `{ status }` → `Box`; 409 `BOX_STATUS_TRANSITION` |
| `DELETE /boxes/{box_id}` | `deleteBox` | У | → 204; вещи `IN_BOX` удаляются каскадом, номер освобождается |
| `PUT /boxes/{box_id}/contents-reminder` | `setBoxContentsReminder` | создатель | `{ remind_at }` → `Box`; 403 `NOT_BOX_CREATOR`, 409 `BOX_NOT_PACKING`, 422 если время в прошлом |
| `DELETE /boxes/{box_id}/contents-reminder` | `cancelBoxContentsReminder` | создатель | → 204 |

**Фильтры `listBoxes`:** `status` (можно повторять: `?status=PACKED&status=LOADED`), `destination_room_id`, `number` (точный номер, в том числе среди `UNPACKED`), `limit`, `after`.

**`POST /projects/{project_id}/boxes`**

```json
{
  "destination_room_id": "0192…",
  "title": "Посуда на первое время",
  "is_fragile": true,
  "items": {
    "source_room_id": "0192…",
    "priority": "MEDIUM",
    "entries": [{ "name": "Кружки", "quantity": 6 }, { "name": "Чайник" }]
  }
}
```

- `items` необязательно. Если передано, вещи создаются в той же транзакции сразу в `IN_BOX`, а статус коробки остаётся `PACKING`.
- Номер — минимальный свободный (раздел 6 db-design.md).

**Смена статуса:**
- Шаг вперёд или назад — 200.
- Повтор текущего статуса — 200 без изменений.
- Остальное — 409, в `details.current` фактический статус, чтобы клиент откатил оптимистичное обновление.
- Переход в `PACKED` отменяет напоминание о содержимом.

**Напоминание о содержимом:** адресат всегда создатель коробки. UI на фронтенде появится вместе с Web Push.

---

## 7. Вещи и поиск

| Метод и путь | operationId | Доступ | Описание |
|--------------|-------------|--------|----------|
| `GET /projects/{project_id}/items` | `listItems` | У | → `Page<Item>`, по `priority, lower(name), id` |
| `POST /projects/{project_id}/items` | `createItem` | У | → 201 `Item` |
| `POST /projects/{project_id}/items/batch` | `createItems` | У | → 201 `Item[]` |
| `GET /items/{item_id}` | `getItem` | У | → `Item` |
| `PATCH /items/{item_id}` | `updateItem` | У | `{ name, quantity, priority, source_room_id }` → `Item`; 422 `ROOM_KIND_MISMATCH` |
| `PUT /items/{item_id}/state` | `setItemState` | У | `{ state, box_id? }` → `Item`; 409 `ITEM_STATE_TRANSITION` |
| `DELETE /items/{item_id}` | `deleteItem` | У | → 204 |
| `GET /items/{item_id}/suggestions` | `getItemSuggestions` | У | → `{ box: BoxRef, matches: number }[]` (запрос 5.3) |
| `GET /projects/{project_id}/items/search?q=&limit=&offset=` | `searchItems` | У | → `{ results: Item[], has_more: boolean }` |

**Фильтры `listItems`:** `state` (можно повторять), `priority`, `box_id`, `source_room_id`, `limit`, `after`. Эти фильтры покрывают:

| Экран | Запрос |
|-------|--------|
| Список для упаковки | `?state=TO_PACK` |
| Содержимое коробки | `?box_id=…` |
| Первый день | `?priority=FIRST_NECESSITY` |
| Потеряшки | `?state=LOST` |

**`POST /projects/{project_id}/items`**: `{ name, source_room_id, quantity?, priority?, box_id? }`. При переданном `box_id` вещь создаётся в `IN_BOX`, иначе в `TO_PACK`. По умолчанию `quantity` = 1, `priority` = `MEDIUM`.

**`POST /projects/{project_id}/items/batch`**: `{ source_room_id, priority?, box_id?, entries: [{ name, quantity? }] }`, до 100 записей. Создание атомарное: либо все вещи, либо ни одной.

**`PUT /items/{item_id}/state`:**
- `box_id` обязателен, если `state = IN_BOX`, и запрещён в остальных случаях.
- Повтор того же состояния (для `IN_BOX` — с той же коробкой) возвращает 200 без изменений.
- Переход вне матрицы 5.2 → 409.

**Поиск:**
- Полнотекстовый, по началу слов (раздел 7 db-design.md). Пустой `q` даёт пустой результат.
- Пагинация через `limit`/`offset`, потому что сортировка идёт по рангу.
- Если запрос — число, фронтенд параллельно вызывает `listBoxes?number=…`.

---

## 8. Задачи и шаблоны

| Метод и путь | operationId | Доступ | Описание |
|--------------|-------------|--------|----------|
| `GET /task-templates` | `listTaskTemplates` | В | → `TaskTemplate[]`, по `phase` |
| `POST /projects/{project_id}/tasks/from-templates` | `createTasksFromTemplates` | У | `{ template_ids: string[] }` → 201 `Task[]`; 422 `UNKNOWN_TEMPLATE` |
| `GET /projects/{project_id}/tasks?phase=&is_done=` | `listTasks` | У | → `Task[]`, по `phase, due_at NULLS LAST, id` |
| `POST /projects/{project_id}/tasks` | `createTask` | У | → 201 `Task` |
| `GET /tasks/{task_id}` | `getTask` | У | → `Task` |
| `PATCH /tasks/{task_id}` | `updateTask` | У | `{ title, description, phase, due_at }` → `Task` |
| `POST /tasks/{task_id}/complete` | `completeTask` | У | → `Task`, идемпотентно |
| `POST /tasks/{task_id}/reopen` | `reopenTask` | У | → `Task`, идемпотентно |
| `PUT /tasks/{task_id}/assignees` | `setTaskAssignees` | У | `{ user_ids: string[] }` → `Task`; 422 `ASSIGNEE_NOT_COLLABORATOR` |
| `PUT /tasks/{task_id}/reminders` | `setTaskReminders` | У | `{ offsets_hours: number[] }` → `Task`; 422 `REMINDER_REQUIRES_DUE_AT` |
| `DELETE /tasks/{task_id}` | `deleteTask` | У | → 204 |

- `id` шаблона — стабильная строка из файла шаблонов (`"notify-management-company"`), а не UUID.
- **`POST /projects/{project_id}/tasks`**: `{ title, description?, phase?, due_at?, assignee_ids?, reminder_offsets_hours? }`, по умолчанию `phase` = `BEFORE`.
- **Срок:** `PATCH` с `due_at: null` удаляет напоминания задачи. Смена `due_at` пересчитывает ожидающие уведомления.
- **Ответственные:** `setTaskAssignees` заменяет список целиком. Новым ответственным, кроме назначившего себя, создаётся `TASK_ASSIGNED`.
- **Напоминания:** смещения от 1 до 8760 часов. UI появится вместе с Web Push.

---

## 9. QR-код

| Метод и путь | operationId | Доступ | Описание |
|--------------|-------------|--------|----------|
| `GET /boxes/by-qr/{qr_token}` | `getBoxByQr` | см. ниже | → `BoxByQr` |

```ts
BoxByQr = {
  access: "READ" | "WRITE",
  project: { id, name },
  box: {
    id, number, title, is_fragile,
    destination_room: RoomRef,
    status: BoxStatus,
    items: { id, name, quantity }[],
  },
}
```

| Режим проекта | Без входа | Участник | Вошедший, не участник |
|---------------|-----------|----------|-----------------------|
| `PROTECTED` | 401 `AUTH_REQUIRED` | 200, `WRITE` | 404 |
| `OPEN` | 200, `READ` | 200, `WRITE` | 200, `READ` |

- Токен удалённой коробки → 404.
- Изменения участник делает обычными эндпоинтами по `box.id`.
- Страница фронтенда: `/b/{qr_token}`. В QR кодируется ссылка на фронтенд, а не на API.

---

## 10. Сводки для экранов

| Метод и путь | operationId | Доступ | Экран |
|--------------|-------------|--------|-------|
| `GET /projects/{project_id}/summary` | `getProjectSummary` | У | Обзор, Прогресс, План |
| `GET /projects/{project_id}/loading-checklist` | `getLoadingChecklist` | У | Прогресс |
| `GET /projects/{project_id}/unpacking-progress` | `getUnpackingProgress` | У | Прогресс |
| `GET /projects/{project_id}/labels?box_ids=` | `getBoxLabels` | У | Этикетки |

```ts
ProjectSummary = {
  boxes: { total: number, by_status: Record<BoxStatus, number> },
  tasks: { total: number, done: number, by_phase: Record<TaskPhase, { total: number, done: number }> },
  items: { total: number, by_state: Record<ItemState, number> },
  first_necessity: { total: number, taken_out: number },
  next_task: Task | null,   // невыполненная, по phase, due_at NULLS LAST, id
}

LoadingChecklistRoom = {
  room: RoomRef,            // kind = SOURCE
  to_pack_count: number,    // вещи TO_PACK из этой комнаты
  boxes: BoxRef[],          // коробки с вещами IN_BOX из этой комнаты
  is_loaded: boolean,       // to_pack_count = 0 и все boxes в LOADED или дальше
}

UnpackingProgressRoom = {
  room: RoomRef,            // kind = DESTINATION
  total: number,
  unpacked: number,
}

BoxLabel = {
  id, number, title: string | null, is_fragile: boolean,
  destination_room: RoomRef,
  qr_token: string,
  item_names: string[],
}
```

- `loading-checklist` и `unpacking-progress` отдают массивы по всем активным комнатам нужного вида, по имени комнаты, в том числе комнаты без коробок.
- `labels` без `box_ids` отдаёт все активные коробки по `number`. `box_ids` — повторяемый параметр для печати выбранных коробок.

---

## 11. Web Push (фронтенд подключит позже)

| Метод и путь | operationId | Доступ | Описание |
|--------------|-------------|--------|----------|
| `GET /push/vapid-public-key` | `getVapidPublicKey` | А | → `{ public_key }` |
| `PUT /push/subscriptions` | `upsertPushSubscription` | В | `{ endpoint, keys: { p256dh, auth } }` → 204, upsert по `endpoint` |
| `DELETE /push/subscriptions?endpoint=` | `deletePushSubscription` | В | → 204 |

Бэкенд реализует их сразу вместе с очередью уведомлений. Пока подписок нет, уведомления получают статус `SKIPPED`.

---

## 12. Чего сейчас нет

- **Чтение журнала событий.** Бэкенд ведёт `AuditLog`, но UI не нужен (решение 6).
- **Восстановление удалённых объектов.** Удаление мягкое, но для пользователя необратимое.
- **Смена пароля и имени пользователя.** В ТЗ не требуется.

---

## 13. Экраны фронтенда → эндпоинты

| Экран | Эндпоинты |
|-------|-----------|
| `/login` | `register`, `login` |
| Все защищённые страницы | `refreshToken`, `getMe`, `listProjects` |
| `/projects/new` | `createProject` |
| Настройки проекта | `getProject`, `updateProject`, `deleteProject`, `listCollaborators`, `addCollaborator`, `removeCollaborator`, `listRooms`, `createRoom`, `updateRoom`, `deleteRoom` |
| `/dashboard` | `getProjectSummary` |
| `/boxes` | `listBoxes`, `createBox`, `searchItems`, `listRooms` |
| Карточка коробки | `getBox`, `updateBox`, `setBoxStatus`, `deleteBox`, `listItems?box_id`, `createItem`, `createItems`, `setItemState`, `updateItem`, `deleteItem` |
| Список для упаковки | `listItems?state=TO_PACK`, `setItemState` |
| `/plan` | `listTasks`, `createTask`, `updateTask`, `completeTask`, `reopenTask`, `deleteTask`, `setTaskAssignees`, `listTaskTemplates`, `createTasksFromTemplates`, `listCollaborators` |
| `/labels` | `getBoxLabels` |
| `/progress` | `getProjectSummary`, `getLoadingChecklist`, `getUnpackingProgress`, `listItems?priority=FIRST_NECESSITY`, `listItems?state=LOST`, `getItemSuggestions`, `setItemState` |
| `/b/{qr_token}` | `getBoxByQr` |
| Позже, вместе с Web Push | `getVapidPublicKey`, `upsertPushSubscription`, `deletePushSubscription`, `setProjectReminders`, `setTaskReminders`, `setBoxContentsReminder`, `cancelBoxContentsReminder` |
