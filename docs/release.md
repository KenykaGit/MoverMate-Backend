# Выпуск изменений и миграции

## Путь от изменения до продакшена

```text
commit ─▶ MR ─▶ check (параллельно) ─▶ build ─▶ merge ─▶ build + push ─▶ deploy (вручную)
               lint · typecheck · architecture · test · migrations · nginx -t
```

1. **Локально:** `make check` выполняет то же, что стадия `check` в CI. pre-commit
   (`make install`) запускает ruff, mypy, import-linter и unit-тесты на каждый коммит.
2. **MR:** параллельные проверки, затем сборка образа без публикации.
3. **Основная ветка:** Kaniko публикует образ `${DOCKER_ARTIFACTORY_REGISTRY}/<namespace>/<project>:<short-sha>`
   (шаблоны Spirit лежат в репозитории: [.gitlab/ci/](../.gitlab/ci/)). Версия
   зашивается в образ (`APP_VERSION`) и видна в `/health/ready`. Повторная сборка того же
   коммита безопасна: существующий тег считается успехом.
4. **Deploy** (кнопка в пайплайне основной ветки; вход на сервер через Teleport; CI кладёт на VM
   docker-конфиг с IAM-токеном Spirit в `$DEPLOY_DIR/.docker`, `docker compose` читает его через
   `DOCKER_CONFIG`) копирует `docker-compose.prod.yml` и
   `docker/nginx/nginx.conf` в `$DEPLOY_DIR` и выполняет на сервере:
   1. `docker compose pull api`;
   2. `docker compose run --rm migrate` — `alembic upgrade head` тем же образом. Если миграция
      упала, job красный, а старый api продолжает обслуживать трафик;
   3. `docker compose up -d --wait api nginx` — ждёт HEALTHCHECK обоих контейнеров;
   4. `nginx -t && nginx -s reload` — конфиг nginx едет вместе с коммитом;
   5. smoke-тест: `/health/ready` через nginx должен вернуть `version` нового коммита.

## Правила миграций

- **Номера последовательные:** `0001_initial.py`, `0002_...`. `make revision m="..."`
  подставляет следующий номер. Если две ветки добавили `0002`, после слияния будет две
  головы: это ловят `alembic heads` в CI и тест `test_single_head`. Перенумеруйте свою
  миграцию и поправьте `down_revision`.
- **Двусторонняя проверка** (`make migrations-check`, job `migrations`): single head →
  `upgrade head` → `alembic check` (модели совпадают со схемой) → `downgrade base` (каждый
  `downgrade()` работает) → `upgrade head`. Интеграционные тесты дополнительно сверяют
  предикаты частичных индексов (autogenerate их не видит) и CHECK-ограничения.
- **Миграции — застывшая история.** Они не импортируют код приложения: константы домена
  (длины, списки значений) переписываются литералами. Изменение домена требует новой миграции.
- **Обратная совместимость с предыдущей версией кода.** Во время деплоя старый api работает
  на новой схеме. Поэтому используется expand/contract:
  1. релиз N: добавить nullable-колонку или новую таблицу; код пишет в старое и новое;
  2. релиз N+1: бэкфилл, код читает новое;
  3. релиз N+2: удалить старое.

  Нельзя за один релиз переименовывать колонку или делать NOT NULL без default.
- **Инварианты в БД:** CHECK, UNIQUE (частичные для soft delete), FK. Перечисления
  (статусы и т. п.) храните как VARCHAR + CHECK, а не нативный ENUM: новое значение
  добавляется заменой constraint, без `ALTER TYPE` ([ADR 0005](adr/0005-migrations.md)).
- Тяжёлые операции (индекс на большой таблице) выполняйте через
  `CREATE INDEX CONCURRENTLY` в отдельной миграции с `autocommit_block()`.

## Откат

- **Код:** перезапустите job `deploy` из пайплайна последнего хорошего коммита. Образ уже
  лежит в registry, сборка не нужна.
- **Схема:** миграции обратно совместимы (см. expand/contract), поэтому старый код работает
  на новой схеме и откат кода не требует `downgrade`. `alembic downgrade` на проде — крайняя
  мера: делается вручную после бэкапа (`docker compose exec db pg_dump ...`).
- **Конфиг nginx** откатывается вместе с коммитом: deploy копирует `docker/nginx/` и делает reload.

## Обновление версий инфраструктуры

Все версии закреплены точно: Python-зависимости `==` в `pyproject.toml` плюс `uv.lock`,
Python в `.python-version`, uv в `[tool.uv] required-version`, Docker-образы — полным тегом
(`postgres:18.6-alpine3.24`, `valkey/valkey:9.1.2-alpine3.24`, `nginx:1.30.5-alpine3.24`…)
в `docker-compose*.yml`, `.gitlab-ci.yml` и `tests/integration/conftest.py`. Обновление —
обычный MR: поменять версии во всех местах, `uv lock`, `make check`.

**Мажорная версия PostgreSQL** (например, 17 → 18) не читает каталог данных предыдущей.
Кроме того, с 18-й версии образ монтирует том в `/var/lib/postgresql` (данные в
`18/docker`), а не в `/var/lib/postgresql/data`. Старый том подключится без ошибки, но
Postgres создаст рядом **пустой** кластер. Поэтому порядок такой:

1. до деплоя: `docker compose exec db pg_dumpall -U "$POSTGRES_USER" > backup.sql`;
2. остановить стек, удалить или переименовать том `pg-data`;
3. задеплоить новую версию: поднимется пустой кластер 18;
4. `docker compose exec -T db psql -U "$POSTGRES_USER" -d postgres < backup.sql`, затем
   проверить `/health/ready` и данные.

Локально достаточно `make db-reset`. Без простоя мажорную версию обновляют через
`pg_upgrade` или логическую репликацию.

## Первичная подготовка сервера (один раз)

1. Установить Docker и плагин compose, создать `DEPLOY_DIR` (например, `/opt/service`).
2. Секреты приложения хранятся в переменных CI (п. 4). Deploy сам записывает
   `$DEPLOY_DIR/.env` (права `600`, пересоздаётся при каждом запуске), чтобы ручные команды
   `docker compose` на сервере тоже работали. С `ENV=prod` приложение не стартует, если секрет
   оставлен по умолчанию или `LOG_JSON=false`.
3. На VM: пользователь `DEPLOY_SSH_USER` с беспарольным `sudo` (deploy сам создаёт `$DEPLOY_DIR` и
   отдаёт его этому пользователю), доступ
   к Teleport для сервисного аккаунта Spirit, доступ к `registry.spirit.t-education.com` и
   `curl` для smoke-теста. `dp` на VM не нужен, ключ сервисного аккаунта туда не попадает.
4. Переменные CI (Spirit → Repository → CI Variables или Settings → CI/CD → Variables):

   | Переменная | Тип | Назначение |
   |---|---|---|
   | `SPIRIT_DOCKER_SA_KEY` | **File**, не protected | JSON-ключ сервисного аккаунта Spirit: push в registry, Teleport, токен для `docker pull` на VM. Не protected: нужен и в MR (сборка без push) |
   | `DOCKER_ARTIFACTORY_REGISTRY` | Variable | адрес registry Spirit (обычно уже задан на уровне группы) |
   | `DEPLOY_SSH_USER` | Variable | логин на VM, разрешённый ролью Teleport сервисного аккаунта (`Logins` в выводе `tsh login`, например `sa-<group>-spirit-docker-ci`); `root` запрещён |
   | `DEPLOY_VM` | Variable | имя VM в Teleport |
   | `DEPLOY_DIR` | Variable | каталог стека на VM, например `/opt/service` |
   | `POSTGRES_USER`, `POSTGRES_DB` | Variable | пользователь и база Postgres в проде |
   | `POSTGRES_PASSWORD` | Variable, masked + protected | пароль Postgres |
   | `AUTH__SECRET_KEY` | Variable, masked + protected | ключ подписи токенов, >= 32 символов: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
   | `CORS_ORIGINS` | Variable | JSON-список источников, например `["https://app.example.com"]`; по умолчанию `[]` |

   Значения без одинарных кавычек. Смена `POSTGRES_PASSWORD` не меняет пароль в уже созданной
   БД (Postgres читает его только при инициализации тома): нужен ещё `ALTER USER`. Смена
   `AUTH__SECRET_KEY` разлогинит всех пользователей.
5. Если перед nginx стоит балансировщик с TLS, раскомментируйте `set_real_ip_from` в
   [nginx.conf](../docker/nginx/nginx.conf), иначе rate limit будет считать все запросы с IP
   балансировщика.

## Локальная проверка перед MR

```bash
make fmt && make check     # lint, mypy, import-linter, все тесты, migrations-check
make up                    # и вручную: curl localhost:8000/health/ready
```
