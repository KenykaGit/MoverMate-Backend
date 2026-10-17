# Наблюдаемость и диагностика

## Сигналы

| Сигнал | Где | Для чего |
|---|---|---|
| `GET /health/live` | процесс жив, зависимости не проверяются | перезапуск контейнера (Docker `HEALTHCHECK`) |
| `GET /health/ready` | Postgres и Redis | снимать ли инстанс с трафика; `version` показывает задеплоенную версию |
| `GET /metrics` | Prometheus, только изнутри сети (nginx отдаёт 404) | тренды и алерты |
| логи api (stdout, JSON) | `docker compose logs api` | разбор конкретного запроса |
| логи nginx (stdout, JSON) | `docker compose logs nginx` | что видел клиент, время апстрима |
| логи certbot (прод) | `docker compose logs certbot` | выпуск и продление сертификата |

### `/health/ready`

| `status` | HTTP | Значение |
|---|---|---|
| `ok` | 200 | всё работает |
| `degraded` | 200 | Redis недоступен: идемпотентность и rate limit отключены, основной сценарий (включая логин) работает. Инстанс **не** снимается с трафика |
| `unavailable` | 503 | Postgres недоступен: сервис не может выполнить ни один сценарий |

Каждая проверка ограничена таймаутом 2 секунды, проверки выполняются параллельно.
Классификация зависимостей задаётся в `ObservabilityProvider`: Postgres — critical,
Redis — auxiliary. Логика деградации находится в use case `CheckReadiness` и покрыта
unit-тестами.

## Метрики

| Метрика | Метки | Что смотреть |
|---|---|---|
| `http_requests_total` | `method`, `route`, `status` | доля 5xx по маршрутам; всплеск 4xx — сломанный клиент или атака |
| `http_request_duration_seconds` | `method`, `route` | p95/p99 латентности |
| `redis_errors_total` | `op` (`rate_limit`, `idempotency_begin`…) | любой рост означает работу в деградированном режиме |
| `rate_limited_total` | `scope=api\|auth` | клиенты упираются в лимит; рост `auth` — признак перебора паролей |

`route` — шаблон пути (`/api/v1/items/{item_id}`, а не сам id), поэтому кардинальность ограничена.
Несуществующие пути схлопываются в `route="unmatched"`.

Примеры запросов PromQL:

```promql
sum by (route) (rate(http_requests_total{status=~"5.."}[5m]))
  / sum by (route) (rate(http_requests_total[5m]))                         # доля 5xx
histogram_quantile(0.95, sum by (le, route) (rate(http_request_duration_seconds_bucket[5m])))
sum(rate(redis_errors_total[5m])) > 0                                       # деградация
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
| `redis_unavailable` | warning | `op`, `error` | операция Redis не удалась, сработал fail-open |
| `readiness_check_failed` | warning | `dependency`, `error` | зависимость не прошла проверку `/health/ready` |
| `idempotency_record_corrupted` | warning | ключ | запись в Redis не читается (например, после смены формата) |

Пароли, токены и хэши никогда не логируются. DTO с секретами помечены `field(repr=False)`,
в pydantic-схемах используется `SecretStr`.

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
6. **Подозрение на рассинхрон версий.** `version` в `/health/ready` должна совпадать с
   коммитом в GitLab.
7. **Клиенты жалуются на сертификат.** Проверьте издателя и срок командой выше, затем
   `docker compose logs certbot`: `issuance failed` чаще всего означает, что DNS домена не
   указывает на VM или `:80` закрыт снаружи.
