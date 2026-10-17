# reference-backend

Эталонный асинхронный бэкенд на Python. Объединяет два подхода:

- **Clean Architecture + DDD.** Бизнес-ядро (domain, application) написано на чистом Python
  и не знает о FastAPI, SQLAlchemy и Redis. Value Objects и сущности сами следят за своими
  инвариантами. Каждый сценарий оформлен отдельным классом use case. Зависимости собирает
  IoC-контейнер Dishka. Границы слоёв проверяют `import-linter` и AST-тест.
- **Эксплуатационная надёжность.** Единственная точка входа — nginx. Redis (сервер Valkey)
  используется для идемпотентности и rate limit и работает в режиме fail-open. Есть метрики Prometheus,
  JSON-логи со сквозным `request_id`, раздельные liveness/readiness, миграции с двусторонней
  проверкой и CI/CD. Уведомления (Web Push) отправляет воркер taskiq: планировщик будит его
  через Redis, а очередь и источник истины — таблица в Postgres.

Готовый домен — пользователи: регистрация и вход по username, сессии с ротацией
refresh-токенов и отзывом по семьям, перехэширование паролей при входе, смена пароля и
username, мягкое удаление. Бизнес-модули продукта добавляются рядом по чек-листу
([architecture.md](docs/architecture.md#как-добавить-модуль)).

Стек (версии закреплены точно, см. [release.md](docs/release.md#обновление-версий-инфраструктуры)):
Python 3.14.8, FastAPI 0.143, Dishka 1.10, SQLAlchemy 2.1 (asyncio) + asyncpg, Alembic 1.20,
PostgreSQL 18.6, Valkey 9.1 (сервер с протоколом Redis, клиент redis-py 8.1), argon2-cffi,
PyJWT, structlog, prometheus-client, taskiq 0.13 + taskiq-redis 1.2 (фоновые задачи),
pywebpush 2.5 (Web Push, VAPID), nginx 1.30, uv 0.13,
pytest + testcontainers, ruff, mypy (strict), import-linter.

## Быстрый старт

Нужны Docker и [uv](https://docs.astral.sh/uv/) 0.13 (нужную версию Python uv поставит сам).

```bash
cp .env.example .env
make up          # nginx :8000 -> api -> postgres + redis, worker + scheduler; миграции применяются сами
curl localhost:8000/health/ready
# {"status":"ok","version":"dev","checks":{"postgres":"ok","redis":"ok","notifications":"ok"}}
```

Сценарий целиком:

```bash
B=localhost:8000/api/v1
curl -X POST $B/auth/register -H 'content-type: application/json' \
  -d '{"username":"me_demo","password":"correct horse battery"}'
TOKENS=$(curl -s -X POST $B/auth/login -H 'content-type: application/json' \
  -d '{"username":"me_demo","password":"correct horse battery"}')
curl $B/users/me -H "Authorization: Bearer $(echo "$TOKENS" | jq -r .access_token)"
curl -X POST $B/auth/refresh -H 'content-type: application/json' \
  -d "{\"refresh_token\":\"$(echo "$TOKENS" | jq -r .refresh_token)\"}"
```

Swagger UI доступен на <http://localhost:8000/docs>.

## Разработка на хосте

```bash
make install     # зависимости из uv.lock и хуки pre-commit
make infra       # только postgres и redis в Docker
make migrate     # alembic upgrade head (DATABASE__URL из .env)
make run         # uvicorn с автоперезагрузкой на :8000
# воркер уведомлений: два процесса taskiq; ключ VAPID создаётся в .vapid/
uv run taskiq worker app.worker.entry:broker --no-configure-logging --workers 1
uv run taskiq scheduler app.worker.entry:scheduler --no-configure-logging
```

## Команды

| Команда | Что делает |
|---|---|
| `make up` / `make down` | весь стек в Docker / остановить (данные сохраняются) |
| `make infra` | только postgres и redis |
| `make logs` | логи api, worker, scheduler и nginx в реальном времени |
| `make run` | API на хосте с `--reload` |
| `make migrate` | применить миграции |
| `make revision m="..."` | автогенерация миграции с очередным номером (`0002_...`) |
| `make lint` | ruff, ruff format --check, mypy strict, import-linter |
| `make fmt` | автоисправление и форматирование |
| `make test` | все тесты с покрытием; интеграционные поднимают Postgres и Redis в testcontainers |
| `make test-unit` | только быстрые тесты без Docker (пара секунд): домен, use cases на фейках, архитектура |
| `make test-local` | все тесты на локальном стеке (`make infra`) вместо testcontainers |
| `make migrations-check` | single head → upgrade → `alembic check` → downgrade base → upgrade |
| `make check` | lint + test + migrations-check: то же, что CI до сборки |
| `make db-reset` | удалить тома локального стека (с подтверждением): БД и ключ VAPID, браузерам придётся подписаться заново |

## Структура

```text
src/app/
├── domain/          чистый Python: сущности, Value Objects (users, tokens, projects, inventory, tasks…)
├── application/     use cases, порты (interfaces/), DTO, исключения; только stdlib
├── infrastructure/  адаптеры: SQLAlchemy, Redis (fail-open), argon2/JWT, Web Push, брокер taskiq, Prometheus, structlog
├── presentation/    FastAPI: роуты (DishkaRoute), схемы, контракт ошибок, middleware
├── ioc/             composition root: провайдеры Dishka (порт → адаптер, scope) и сборка контейнера
├── config.py        pydantic-settings
├── main.py          точка входа api: uvicorn app.main:create_app --factory
└── worker/          воркер уведомлений на taskiq: задача, планировщик; entry.py — цели CLI taskiq
tests/
├── fakes.py         in-memory реализации всех портов
├── unit/            домен, use cases, архитектура: пара секунд, без Docker
└── integration/     репозитории, миграции, Redis, отказ Redis, E2E API
```

Подробности: [docs/architecture.md](docs/architecture.md).

## Тесты

- **Unit** (`make test-unit`) проверяют домен и все use cases на in-memory фейках портов
  ([tests/fakes.py](tests/fakes.py)), опубликованный OpenAPI-контракт, проводку DI-контейнера
  (scope, общая сессия, настройки адаптеров, регистрация каждого use case) и правила архитектуры
  ([tests/unit/test_architecture.py](tests/unit/test_architecture.py)). Docker не нужен, прогон занимает пару секунд.
- **Integration** (`tests/integration/`, метка `integration` ставится автоматически)
  работают с настоящими PostgreSQL и Redis. Схема создаётся реальными миграциями Alembic.
  Тесты покрывают репозитории, частичные индексы, CHECK-ограничения, SAVEPOINT и
  конкурентные транзакции, отзыв семей refresh-токенов, перехэширование при смене параметров
  argon2, адаптеры Redis, работу API при отказе Redis и E2E-сценарии через `httpx.AsyncClient`.
- По умолчанию Postgres и Redis запускаются в testcontainers. Если заданы
  `TEST_DATABASE_URL` (имя БД оканчивается на `_test`) и `TEST_REDIS_URL` (логическая БД 15),
  используются эти серверы: так тесты работают в CI и в `make test-local`.

## CI/CD

[.gitlab-ci.yml](.gitlab-ci.yml) состоит из трёх стадий:

1. `check` запускает параллельно `lint`, `typecheck`, `architecture`, `test`, `migrations`
   и `nginx -t`.
2. `build` собирает Docker-образ. В merge request образ только собирается, на основной
   ветке ещё и публикуется в registry Spirit (Kaniko, шаблоны Spirit в
   [.gitlab/ci/](.gitlab/ci/)).
3. `deploy` запускается вручную через Teleport: миграции, затем `up --wait` (api, worker,
   scheduler, certbot, nginx), reload nginx и smoke-тест версии.

Процесс выпуска и отката описан в [docs/release.md](docs/release.md).

## Документация

- [docs/architecture.md](docs/architecture.md): слои, потоки данных, DI, модель данных, контроль архитектуры
- [docs/api.md](docs/api.md): эндпоинты, формат ошибок, идемпотентность, rate limit
- [docs/observability.md](docs/observability.md): метрики, логи, health checks, разбор инцидентов
- [docs/release.md](docs/release.md): выпуск, правила миграций, обновление версий, откат
- [docs/adr/](docs/adr/): архитектурные решения 0001–0011

## Как шаблон закрывает критерии жюри (backend)

Первоисточник — `Критерии.pdf`, раздел «Критерии жюри — Backend». Подробные правила, которые
нельзя нарушать, перечислены в [CLAUDE.md](CLAUDE.md#критерии-жюри-backend).

| Критерий | Где смотреть |
|---|---|
| API и серверные контракты | [docs/api.md](docs/api.md), единый формат ошибок в [errors.py](src/app/presentation/http/errors.py) (и у nginx), `/docs`; тесты контракта: [test_openapi.py](tests/unit/presentation/test_openapi.py), [test_error_contract.py](tests/unit/presentation/test_error_contract.py) |
| Архитектура сервиса | [docs/architecture.md](docs/architecture.md#слои), [ADR 0001](docs/adr/0001-clean-architecture-and-ddd.md), [ADR 0002](docs/adr/0002-dishka-ioc.md); границы проверяют [.importlinter](.importlinter), [test_architecture.py](tests/unit/test_architecture.py) и [тесты контейнера](tests/unit/ioc/test_container_wiring.py) |
| Архитектура системы | [docs/architecture.md](docs/architecture.md#система), [docker-compose.yml](docker-compose.yml), [nginx.conf](docker/nginx/nginx.conf), [ADR 0003](docs/adr/0003-postgres-and-redis-fail-open.md), [ADR 0006](docs/adr/0006-nginx-edge-and-rate-limiting.md), [ADR 0011](docs/adr/0011-notification-worker.md) (воркер уведомлений) |
| Архитектурные компромиссы и ограничения | [docs/adr/](docs/adr/) (0001–0012, у каждого раздел «Альтернативы»), [известные ограничения](docs/architecture.md#известные-ограничения) |
| Безопасное добавление изменений | обязательная стадия `check` в [.gitlab-ci.yml](.gitlab-ci.yml), pre-commit с unit-тестами, `make check`; 400+ тестов в [tests/](tests/) |
| Выпуск изменений и обновление схемы | [docs/release.md](docs/release.md) (путь до прода, откат, обновление версий), [ADR 0005](docs/adr/0005-migrations.md), [migrations/](migrations/), сервис `migrate` и job `deploy` |
| Модель данных и переходы состояний | [состояния и переходы](docs/architecture.md#состояния-и-переходы), [сущности](src/app/domain/), ограничения БД в [0001_initial.py](migrations/versions/0001_initial.py), [ADR 0007](docs/adr/0007-authentication-and-sessions.md), [ADR 0008](docs/adr/0008-soft-delete-and-savepoints.md), [test_migrations.py](tests/integration/migrations/test_migrations.py) |
| Наблюдаемость и диагностика | [docs/observability.md](docs/observability.md), `/health/live`, `/health/ready` (`degraded`, в том числе при отставании очереди уведомлений), `/metrics`, JSON-логи api и nginx со сквозным `request_id`, healthcheck воркера |
| Проверка бизнес-логики и контрактов | [tests/unit](tests/unit) (домен, use cases на фейках, контракты), [tests/integration](tests/integration) (Postgres, Redis, отказ Redis, E2E) |

## Использование ИИ

**В основе — два шаблона.** reference-backend объединяет
`backend-template-v0` (эксплуатационная обвязка: nginx, CI/CD, Redis fail-open,
наблюдаемость, миграции) и `clean-architecture-backend-template` (автор LeyViaFun: слои
Clean Architecture, Value Objects, use cases пользователей и аутентификации). Часть кода
обоих шаблонов перенесена с доработками, в том числе дословно.

> Команда: укажите, как создавалась первая версия reference-backend (вручную или с ИИ),
> и подтвердите, что у вас есть право использовать `clean-architecture-backend-template`:
> в его репозитории нет файла лицензии.

**Что сделал ИИ.** Изменения от 12 октября 2026 года выполнены ИИ-агентом
[Claude Code](https://claude.com/claude-code) (Anthropic, модель Claude Opus 5.5) по
заданию команды:
- сравнение трёх шаблонов;
- обновление стека до последних стабильных версий с точным закреплением;
- удаление демонстрационного модуля `tasks`;
- семейства refresh-токенов и перехэширование паролей при входе;
- тесты DI-контейнера и OpenAPI;
- этот раздел и таблица критериев.

Агент запускал ruff, mypy, import-linter, тесты на testcontainers, сборку образа, проверку
`nginx -t` и smoke-сценарий на поднятом стеке.

**Что сделала команда.** Ставила задачи и принимала решения по предложенным вариантам.
Например, выбрала Valkey вместо Redis 8 из-за лицензии, решила удалить `tasks`, определила,
какие пробелы относительно двух других шаблонов закрыть.

**Как ИИ используется дальше.** [CLAUDE.md](CLAUDE.md) содержит инструкции для агента:
критерии жюри и обязательные правила проекта. Коммиты с участием агента помечаются строкой
`Co-Authored-By: Claude`.

**Сторонний код.** Кроме двух шаблонов выше, код написан по официальной документации
библиотек (Alembic, Dishka, SQLAlchemy, nginx) и использует распространённые шаблоны.
Внешние зависимости и их версии перечислены в `pyproject.toml` и `uv.lock`.

> Команда: дополняйте этот раздел по мере работы над кейсом (какие части написаны с ИИ, какие вручную).
