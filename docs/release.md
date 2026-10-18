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
   `DOCKER_CONFIG`) копирует `docker-compose.prod.yml`, prod-конфиг nginx (`docker/nginx/`)
   скрипт certbot (`docker/certbot/`) и конфиг Vector (`docker/vector/`) в `$DEPLOY_DIR` и
   выполняет на сервере:
   1. `docker compose pull api` — тот же образ (и тег) используют `worker` и `scheduler`;
   2. `docker compose run --rm migrate` — `alembic upgrade head` тем же образом. Если миграция
      упала, job красный, а старый api продолжает обслуживать трафик;
   3. `docker compose up -d --wait api worker scheduler certbot nginx` — ждёт HEALTHCHECK
      контейнеров (у `scheduler` его нет: ждётся запуск). Старый `worker` получает SIGTERM,
      taskiq ждёт текущую задачу до 30 с (`--wait-tasks-timeout`, `stop_grace_period: 35s`);
      уведомление, прерванное до коммита, остаётся `PENDING` и уйдёт с новым воркером
      ([ADR 0012](adr/0012-notification-worker.md)). Сигналы, поставленные `scheduler`, пока
      воркер перезапускался, ждут в Redis и разбираются пустыми запусками. Новый воркер healthy
      сразу после старта (heartbeat при `WORKER_STARTUP`) и остаётся таким, пока запуски
      задачи проходят успешно. certbot при первом запуске кладёт self-signed
      заглушку, выпускает сертификат Let's Encrypt для `DOMAIN` и затем сам продлевает его
      ([ADR 0009](adr/0009-tls-letsencrypt-certbot.md));
   4. `nginx -t && nginx -s reload` — конфиг nginx едет вместе с коммитом;
   5. `docker compose up -d --force-recreate vector`, если задан `SAGE__API_KEY` (иначе
      `vector` удаляется) — отправка логов в Sage с новым конфигом
      ([ADR 0017](adr/0017-logs-in-sage.md)). Без `--wait`: сбой `vector` выводит
      предупреждение, но не валит деплой;
   6. smoke-тест: `https://127.0.0.1/health/ready` должен вернуть `version` нового коммита
      (`curl -k`: сразу после первого деплоя ещё может отдаваться заглушка). Если доверенного
      сертификата для `DOMAIN` пока нет, job печатает предупреждение, но не падает.

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
  лежит в registry, сборка не нужна. `worker` и `scheduler` откатываются вместе с api: у них
  один тег образа.
- **Схема:** миграции обратно совместимы (см. expand/contract), поэтому старый код работает
  на новой схеме и откат кода не требует `downgrade`. `alembic downgrade` на проде — крайняя
  мера: делается вручную после бэкапа (`docker compose exec db pg_dump ...`).
- **Конфиг nginx** откатывается вместе с коммитом: deploy копирует `docker/nginx/` и делает reload.
  Тома `letsencrypt` и `tls` откат не трогает: сертификат остаётся на месте. Том `vapid`
  тоже: ключ VAPID и подписки браузеров переживают откат.

## Обновление версий инфраструктуры

Все версии закреплены точно: Python-зависимости `==` в `pyproject.toml` плюс `uv.lock`,
Python в `.python-version`, uv в `[tool.uv] required-version`, Docker-образы — полным тегом
(`postgres:18.6-alpine3.24`, `valkey/valkey:9.1.2-alpine3.24`, `nginx:1.30.5-alpine3.24`,
`timberio/vector:0.58.0-alpine`…)
в `docker-compose*.yml`, `.gitlab-ci.yml` и `tests/integration/conftest.py`. Транзитивные
зависимости pywebpush (`aiohttp`, `multidict`) закреплены через
`[tool.uv] constraint-dependencies` на релизах не моложе двух недель. Обновление — обычный
MR: поменять версии во всех местах, `uv lock`, `make check`.

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
   | `DOMAIN` | Variable | публичное имя сервиса для сертификата Let's Encrypt, например `api.example.com` |
   | `CERTBOT_EMAIL` | Variable, необязательная | e-mail аккаунта Let's Encrypt (уведомления о проблемах с аккаунтом); без неё аккаунт регистрируется без почты |
   | `LLM__API_KEY` | Variable, masked + protected, необязательная | ключ аккаунта LLM-прокси Spirit для сканирования вещей по фото ([ADR 0015](adr/0015-photo-scanning-with-llm.md)); без него сканирование отвечает 503 `item_recognition_disabled`, остальное работает |
   | `LLM__SCAN_MODEL` | Variable, необязательная | модель для сканирования в формате прокси `<provider>/<model>`; по умолчанию `openrouter/claude-haiku-5-5` |
   | `SAGE__API_KEY` | Variable, masked + protected, необязательная | Spirit API ключ для отправки логов в Sage сервисом `vector` ([ADR 0017](adr/0017-logs-in-sage.md)); без него логи остаются только на VM |
   | `SEED_TEST_USERS` | Variable, необязательная | `true`: на старте api создаёт пять тестовых аккаунтов с демо-проектами ([ADR 0018](adr/0018-test-accounts-in-prod.md)); по умолчанию `false` |
   | `SEED_TEST_PASSWORD` | Variable, masked + protected | пароль тестовых аккаунтов, 8–128 символов; обязателен при `SEED_TEST_USERS=true` (deploy падает без него), публичный пароль из каталога в проде не принимается |
   | `SAGE__GROUP` | Variable, необязательная | группа Sage для логов; по умолчанию `hack-led-12` |

   Значения без одинарных кавычек. Смена `POSTGRES_PASSWORD` не меняет пароль в уже созданной
   БД (Postgres читает его только при инициализации тома): нужен ещё `ALTER USER`. Смена
   `AUTH__SECRET_KEY` разлогинит всех пользователей.
5. HTTPS ([ADR 0009](adr/0009-tls-letsencrypt-certbot.md)): DNS-запись `A` для `DOMAIN` указывает
   на публичный IP VM, порты `80` и `443` открыты снаружи (группа безопасности облака, firewall
   VM). Let's Encrypt проверяет домен запросом на `http://$DOMAIN/.well-known/acme-challenge/`.
   Проверка после деплоя: `curl -I https://$DOMAIN/health/live`, а при проблемах —
   `docker compose logs certbot`. Том `letsencrypt` не удаляйте: повторный выпуск ограничен
   лимитами LE (5 одинаковых сертификатов в неделю).
6. Web Push ([ADR 0012](adr/0012-notification-worker.md)): VM нужен исходящий HTTPS (`:443`)
   к push-сервисам браузеров (`fcm.googleapis.com`, `updates.push.services.mozilla.com`,
   `*.push.apple.com` и др.). Ключ VAPID создаётся сам при первом использовании в томе `vapid`
   (`/var/lib/app/vapid/private_key.pem`, общий для api и worker). **Том `vapid` не удаляйте**,
   как и `pg-data`: с новым ключом все подписки браузеров перестают работать (push-сервисы
   отвечают 403), и клиентам нужно подписаться заново. Контакт VAPID (`sub`) — CI-переменная
   `PUSH__VAPID_SUBJECT` (`mailto:` или `https://`); без неё — `https://$DOMAIN`.
7. Логи в Sage ([ADR 0017](adr/0017-logs-in-sage.md)): VM нужен исходящий HTTPS к
   `sage.spirit.t-education.com`. Проверка после деплоя: `docker compose ps vector` (healthy),
   затем в Sage запрос `group="hack-led-12" system="api"`. Ошибки отправки — в
   `docker compose logs vector`.
8. Если перед nginx появится балансировщик, который сам завершает TLS, верните прод на
   HTTP-конфиг [nginx.conf](../docker/nginx/nginx.conf) и раскомментируйте `set_real_ip_from` в
   [http.conf](../docker/nginx/http.conf), иначе rate limit будет считать все запросы с IP
   балансировщика.

## Локальная проверка перед MR

```bash
make fmt && make check     # lint, mypy, import-linter, все тесты, migrations-check
make up                    # и вручную: curl localhost:8000/health/ready
```
