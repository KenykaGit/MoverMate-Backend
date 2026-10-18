# Наблюдаемость и диагностика

## Сигналы

| Сигнал | Где | Для чего |
|---|---|---|
| `GET /health/live` | процесс жив, зависимости не проверяются | перезапуск контейнера (Docker `HEALTHCHECK`) |
| `GET /health/ready` | Postgres, Redis, очередь уведомлений, LLM (если сканирование включено) | снимать ли инстанс с трафика; `version` показывает задеплоенную версию |
| `GET /metrics` | Prometheus, только изнутри сети (nginx отдаёт 404) | тренды и алерты |
| логи api (stdout, JSON) | `docker compose logs api` | разбор конкретного запроса |
| логи nginx (stdout, JSON) | `docker compose logs nginx` | что видел клиент, время апстрима |
| логи certbot (прод) | `docker compose logs certbot` | выпуск и продление сертификата |
| healthcheck и логи worker, логи scheduler | `docker compose ps worker scheduler`, `docker compose logs worker scheduler` | отправка уведомлений |

### `/health/ready`

| `status` | HTTP | Значение |
|---|---|---|
| `ok` | 200 | всё работает |
| `degraded` | 200 | Redis недоступен (`checks.redis`): идемпотентность и rate limit отключены, основной сценарий (включая логин) работает. Или отстаёт очередь уведомлений (`checks.notifications`): воркер или планировщик остановлен, воркер не успевает, или Redis недоступен давно (сигналы воркеру не доходят). Или упал последний вызов модели для сканирования по фото (`checks.llm`, см. ниже). Инстанс **не** снимается с трафика |
| `unavailable` | 503 | Postgres недоступен: сервис не может выполнить ни один сценарий |

Каждая проверка ограничена таймаутом 2 секунды, проверки выполняются параллельно.
Классификация зависимостей задаётся в `ObservabilityProvider`: Postgres — critical,
Redis, `notifications` и `llm` — auxiliary. Логика деградации находится в use case `CheckReadiness` и покрыта
unit-тестами.

`checks.llm` есть, только если задан `LLM__API_KEY`: выключенное сканирование — это
настройка, а не сбой. Проверка не обращается к модели (каждый вызов платный и идёт секунды),
а показывает исход последнего настоящего вызова (`LlmCallStatus`): `degraded` от упавшего
скана до следующего успешного. Причина — в `outcome` события `llm_request_failed` и метрики
`llm_requests_total`.

### Воркер уведомлений

Доставку выполняют два процесса taskiq ([ADR 0011](adr/0011-notification-worker.md)):
`scheduler` раз в 15 с кладёт сигнал в Redis, `worker` по сигналу отправляет созревшие
уведомления. Запросов они не принимают, поэтому состояние видно тремя способами:

| Сигнал | Когда срабатывает |
|---|---|
| `checks.notifications = degraded` в `/health/ready` api (`NotificationLagProbe`) | самое старое уведомление в `PENDING` просрочено больше чем на `WORKER__MAX_LAG_SECONDS` (300 с): воркер стоит или не справляется. Время берётся из БД, расхождение часов api на проверку не влияет |
| контейнер `worker` в состоянии `unhealthy` | heartbeat-файл `/tmp/worker-heartbeat` не обновлялся дольше 60 с. Воркер обновляет его при старте и после каждого успешного запуска задачи (раз в 15 с). Устаревший heartbeat значит «доставки нет», причина — любая: стоит воркер, стоит `scheduler`, недоступен Redis (сигналы не доходят) или Postgres (запуск падает). У `scheduler` своего healthcheck нет |
| события `notification_processed` | исход каждого уведомления (см. ниже) |

Метрик Prometheus у воркера нет.

## Метрики

| Метрика | Метки | Что смотреть |
|---|---|---|
| `http_requests_total` | `method`, `route`, `status` | доля 5xx по маршрутам; всплеск 4xx — сломанный клиент или атака |
| `http_request_duration_seconds` | `method`, `route` | p95/p99 латентности |
| `redis_errors_total` | `op` (`rate_limit`, `idempotency_begin`…) | любой рост означает работу в деградированном режиме. `op="task_queue_pop"` считается в процессе воркера, у которого нет `/metrics`: там смотрите логи |
| `rate_limited_total` | `scope=api\|auth\|scan` | клиенты упираются в лимит; рост `auth` — признак перебора паролей |
| `llm_requests_total` | `operation=scan_items`, `outcome` | вызовы модели: `ok`; `timeout` (дольше `LLM__TIMEOUT_SECONDS`), `unreachable` (прокси недоступен), `rate_limited` (429: квота аккаунта прокси), `rejected` (прочие 4xx: ключ, заблокированная модель — чините настройки), `error` (5xx), `bad_answer` (модель ответила не JSON), `truncated` (ответ оборвался на лимите токенов провайдера: сами мы `max_tokens` не передаём) |
| `llm_request_duration_seconds` | `operation` | латентность модели, включая неудачные вызовы; таймаут nginx для сканирования — 45 с |

`route` — шаблон пути (`/api/v1/projects/{project_id}`, а не сам id), поэтому кардинальность ограничена.
Несуществующие пути схлопываются в `route="unmatched"`.

Примеры запросов PromQL:

```promql
sum by (route) (rate(http_requests_total{status=~"5.."}[5m]))
  / sum by (route) (rate(http_requests_total[5m]))                         # доля 5xx
histogram_quantile(0.95, sum by (le, route) (rate(http_request_duration_seconds_bucket[5m])))
sum(rate(redis_errors_total[5m])) > 0                                       # деградация
sum by (outcome) (rate(llm_requests_total{outcome!="ok"}[15m]))             # сбои сканирования
```

## Логи

Логи пишутся в stdout по одной JSON-строке на событие (`LOG_JSON=true`, обязательно в prod).
Локально удобнее цветной вывод (`LOG_JSON=false`). structlog и stdlib (application, uvicorn,
SQLAlchemy) проходят через один конвейер. Каждая строка, записанная в рамках запроса,
содержит `request_id`, а после аутентификации ещё и `user_id`.

```json
{"event": "http_request", "method": "PUT", "path": "/api/v1/users/me/username", "route": "/api/v1/users/me/username", "status": 409, "duration_ms": 6.1, "request_id": "f111…", "user_id": "0191…", "level": "info", "logger": "app.access", "timestamp": "…"}
```

### События

| Событие | Уровень | Поля | Значение |
|---|---|---|---|
| `http_request` | info / warning для 5xx | `method`, `path`, `route`, `status`, `duration_ms` | access-лог, одна строка на запрос |
| `unhandled_error` | error + traceback | `method`, `path` | непредвиденное исключение → 500 |
| `user_registered`, `user_logged_in` | info | `user_id` | аудит |
| `user_logged_out` | info | `user_id`, `family_id` | аудит: завершена одна сессия |
| `password_rehashed` | info | `user_id` | хэш пароля пересчитан с текущими параметрами argon2 при входе |
| `login_failed` | info | `reason=unknown_login\|wrong_password` | без имени пользователя и пароля: опечатка в поле имени пользователя могла бы оказаться паролем |
| **`refresh_token_reused`** | **warning** | `user_id`, `family_id`, `token_id` | возможна кража сессии; эта сессия (семья токенов) отозвана, остальные работают |
| `password_changed`, `user_deleted`, `user_restored` | info | `user_id` | аудит |
| `project_created` | info | `project_id`, `user_id`, `rooms` | создан проект и `rooms` комнат |
| `project_rescheduled` | info | `project_id`, `moving_date` | сменились дата переезда или часовой пояс; ожидающие напоминания пересозданы |
| `project_reminders_changed` | info | `project_id`, `offsets_days` | новый набор напоминаний о переезде (повтор того же набора не пишется) |
| `project_deleted` | info | `project_id`, `user_id`, `cancelled_notifications` | проект удалён, столько ожидающих уведомлений отменено |
| **`project_delete_forbidden`** | **warning** | `project_id`, `user_id` | участник, не владелец, пытался удалить проект (403); серия таких событий — ошибка клиента или попытка злоупотребления |
| `collaborator_added` | info | `project_id`, `user_id`, `collaborator_id` | `user_id` добавил участника (повторное добавление не пишется) |
| `collaborator_removed`, `project_left` | info | `project_id`, `user_id`, `collaborator_id` | участник убран другим участником / вышел сам; его ожидающие уведомления в проекте отменены |
| `box_created` | info | `box_id`, `number`, `items` | новая коробка; `items` — сколько вещей сразу положили в неё |
| `box_status_changed` | info | `box_id`, `from_status`, `to_status`, `rollback` | шаг статуса коробки; `rollback=true` — откат на шаг назад |
| `box_deleted` | info | `box_id`, `items` | коробка удалена вместе с `items` вещами внутри |
| `item_state_changed` | info | `item_id`, `from_state`, `to_state` | вещь упакована, выложена, потеряна или найдена |
| `room_deleted` | info | `room_id`, `user_id` | удалена неиспользуемая комната |
| `items_scanned` | info | `project_id`, `recognized`, `items` | фото распознано: модель назвала `recognized` позиций, после проверки и объединения осталось `items` |
| `llm_request_completed` | info | `operation`, `model`, `duration_seconds`, `prompt_tokens`, `completion_tokens`, `items` | успешный вызов модели; токены — для контроля расхода квоты прокси |
| **`llm_request_failed`** | **warning** | `operation`, `model`, `outcome`, `http_status`, `error`, `duration_seconds` | вызов модели не удался, клиент получил 503 `item_recognition_unavailable`; `outcome` — как в `llm_requests_total`, `error` — начало ответа прокси (до 300 символов) |
| `task_created` | info | `project_id`, `task_id`, `assignees` | новая задача и число её ответственных |
| `tasks_created_from_templates` | info | `project_id`, `template_ids` | выбранные шаблоны скопированы в задачи (повтор `id` в запросе копируется один раз) |
| `task_completed`, `task_reopened` | info | `project_id`, `task_id` | задача выполнена (ожидающие уведомления отменены) / возвращена в работу (напоминания поставлены заново); повтор не пишется |
| `task_assignees_changed` | info | `task_id`, `added`, `removed` | заменены ответственные; снятые потеряли ожидающие уведомления по задаче |
| `task_reminders_changed` | info | `task_id`, `offsets_hours` | новый набор напоминаний задачи (повтор того же набора не пишется) |
| `task_deleted` | info | `project_id`, `task_id`, `user_id` | задача удалена, её ожидающие уведомления отменены |
| `redis_unavailable` | warning | `op`, `error` | операция Redis не удалась, сработал fail-open |
| `readiness_check_failed` | warning | `dependency`, `error` | зависимость не прошла проверку `/health/ready` |
| `idempotency_record_corrupted` | warning | ключ | запись в Redis не читается (например, после смены формата) |
| `push_subscribed` | info | `user_id`, `subscription_id`, `renewed`, `push_service` | браузер подписан; `renewed=true` — `endpoint` уже был известен (повтор, другой аккаунт или оживление); `push_service` — только хост push-сервиса |
| `push_unsubscribed` | info | `user_id`, `subscription_id` | пользователь отписал свой браузер (чужой или неизвестный `endpoint` не пишется) |

События воркера (`docker compose logs worker`, без `request_id`):

| Событие | Уровень | Поля | Значение |
|---|---|---|---|
| `worker_started` | info | `version`, `interval` | воркер запущен (`WORKER_STARTUP`), контейнер зависимостей создан; `interval` — период планировщика в секундах |
| `notifications_drained` | info | `count` | за запуск задачи обработано столько уведомлений (пустые запуски не пишутся) |
| `notification_processed` | info / **warning** для `FAILED` | `notification_id`, `notification_type`, `project_id`, `user_id`, `outcome`, `attempts`, `delay_seconds` | исход уведомления: `SENT`, `SKIPPED` (нет подписок), `CANCELLED` (причина отпала), `RETRY` (все push-сервисы ответили ошибкой, повтор позже), `FAILED` (последняя попытка); `delay_seconds` — опоздание относительно `scheduled_at` |
| `push_subscription_gone` | info | `subscription_id`, `user_id` | push-сервис ответил 404/410 или ключи непригодны: подписка отключена |
| `redis_unavailable` | warning | `op=task_queue_pop`, `error` | воркер не смог прочитать очередь taskiq (Redis недоступен); повтор через 1 с, процесс не перезапускается. Уведомления ждут в Postgres |
| **`worker_iteration_failed`** | **error** + traceback | — | запуск задачи упал (чаще всего недоступна БД); heartbeat не обновляется, следующий сигнал попробует снова |
| `worker_stopped` | info | — | воркер остановлен (`WORKER_SHUTDOWN`), контейнер зависимостей закрыт |

Собственные логи taskiq (логгеры `taskiq.*`, уровень INFO) идут через тот же structlog JSON:
`--no-configure-logging` отключает формат taskiq, `app.worker.entry` настраивает логи при
импорте. Полезные события: `Starting scheduler.` и `Sending task deliver_due_notifications with
schedule_id deliver_due_notifications.` (каждые 15 с) в `docker compose logs scheduler`,
`Listening started.` и `Executing task deliver_due_notifications with ID: …` в логах `worker`.
Если `Sending task` идёт, а `Executing task` нет — сигналы не доходят: смотрите Redis и
`worker`.

Пароли, токены и хэши никогда не логируются. DTO с секретами помечены `field(repr=False)`,
в pydantic-схемах используется `SecretStr`. `endpoint` push-подписки тоже секрет (кто его
знает, может слать push в браузер): события содержат только хост push-сервиса. Ключ
LLM-прокси (`LLM__API_KEY`) хранится как `SecretStr`, а фото и ответы модели целиком в логи
не попадают.

### Логи nginx

На каждый запрос пишется JSON-строка: `request_id`, `remote_addr`, `status`, `upstream_status`,
`request_time`, `upstream_response_time`. Тот же `request_id` есть в логах api, поэтому запрос
находится в обоих компонентах одним `grep`.

- `status=503` при пустом или `502` `upstream_status`: api недоступен, nginx ответил сам
  (`service_unavailable`).
- `request_time` намного больше `upstream_response_time`: тормозит клиент или сеть, а не api.

### Сертификат (прод)

Скрипты certbot и nginx пишут в stderr строки с префиксом, не JSON
([ADR 0009](adr/0009-tls-letsencrypt-certbot.md)):

| Строка | Где | Значение |
|---|---|---|
| `certbot.sh: no certificate yet, writing a self-signed placeholder` | certbot | первый запуск: nginx стартует на заглушке |
| `certbot.sh: waiting for nginx to serve the ACME webroot` | certbot | nginx ещё не поднялся; к Let's Encrypt не обращаемся |
| `certbot.sh: installed the certificate for <domain>` | certbot | выпущен или продлён сертификат, nginx подхватит его в течение минуты |
| **`certbot.sh: issuance failed (...), retrying in 1h`** | certbot | выпуск или продление не удались: проверьте DNS и доступность `:80` снаружи; причина — в строках certbot выше |
| `tls-entrypoint: certificate changed, reloading nginx` | nginx | nginx перечитал сертификат |

Срок действия: `echo | openssl s_client -connect $DOMAIN:443 -servername $DOMAIN 2>/dev/null | openssl x509 -noout -issuer -enddate`.
Издатель `CN=<domain>` вместо Let's Encrypt означает, что всё ещё отдаётся заглушка.

## Как локализовать сбой

1. **Клиент получил ошибку.** Возьмите `request_id` из тела ответа или заголовка и выполните
   `docker compose logs api nginx | grep <request_id>`. Будет видна цепочка событий, а для 500
   и traceback.
2. **Растут 5xx.** `http_requests_total{status=~"5.."}` по `route` покажет затронутый маршрут.
   Затем проверьте `/health/ready`: если Postgres `unavailable`, проблема в БД, иначе ищите
   `unhandled_error`.
3. **Выросла латентность.** Смотрите `http_request_duration_seconds` по `route`. Если при этом
   растёт `redis_errors_total`, Redis отвечает по таймауту: до 2 × 200 мс на каждую
   операцию. Проверьте его первым.
4. **Всплеск 429 на `auth`.** `rate_limited_total{scope="auth"}` вместе с `login_failed` —
   перебор паролей. Лимит работает, пока Redis жив.
5. **`refresh_token_reused` в логах.** Токен пользователя предъявлен повторно: украден или
   клиент повторил запрос после сетевой ошибки. Сессия `family_id` уже отозвана, на этом
   устройстве нужно войти заново. Много таких событий у одного клиента — скорее ошибка
   клиента (параллельные refresh), чем атака.
6. **Пользователь не видит проект или не получил напоминание.** 404 `project_not_found`
   одинаков для «нет проекта» и «не участник»: ищите по `project_id` события
   `collaborator_removed`/`project_left` и `project_deleted`. Сдвиг напоминаний после смены
   даты или пояса виден по `project_rescheduled`. Дальше — п. 9.
7. **Подозрение на рассинхрон версий.** `version` в `/health/ready` должна совпадать с
   коммитом в GitLab.
8. **Клиенты жалуются на сертификат.** Проверьте издателя и срок командой выше, затем
   `docker compose logs certbot`: `issuance failed` чаще всего означает, что DNS домена не
   указывает на VM или `:80` закрыт снаружи.
9. **Уведомление не пришло.**
   1. `/health/ready`: `checks.notifications = degraded` — очередь стоит. `docker compose ps
      worker scheduler`: `worker` в `unhealthy` или не запущен, `scheduler` не запущен.
      `checks.redis = degraded` — сигналы воркеру не доходят, уведомления уйдут после
      возвращения Redis. Иначе причина — в `docker compose logs worker`
      (`worker_iteration_failed`) и `docker compose logs scheduler`.
   2. Воркер жив — найдите уведомление: `docker compose logs worker | grep <notification_id>`
      или по `user_id`, затем `outcome`.
   3. Статус в таблице: `SELECT status, attempts, last_error, scheduled_at, sent_at FROM
      notifications WHERE user_id = '…' ORDER BY scheduled_at DESC`. `PENDING` с будущим
      `scheduled_at` — ещё не время или ждёт повтора; `SKIPPED` — у пользователя нет активной
      подписки (браузер не вызвал `PUT /push/subscriptions` или подписка отпала,
      `push_subscription_gone`); `CANCELLED` — причина отпала (задача выполнена, коробка
      собрана, пользователь вышел из проекта); `FAILED` — смотрите `last_error`.
   4. `SENT`, но пользователь ничего не видел: push-сервис принял сообщение, дальше отвечают
      браузер и ОС (разрешения, режим «Не беспокоить», на iOS — PWA на домашнем экране).
   5. 403 от всех push-сервисов в `last_error` — сменился ключ VAPID (потерян том `vapid`):
      клиентам нужно подписаться заново.
