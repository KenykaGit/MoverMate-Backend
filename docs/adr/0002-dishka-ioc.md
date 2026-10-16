# ADR 0002. IoC-контейнер Dishka

**Статус:** принято

## Контекст

Use cases зависят от портов, а порты реализуются адаптерами с разным временем жизни.
Engine и пул Redis живут всё время работы процесса, сессия БД и UoW живут один запрос.
FastAPI `Depends` умеет только время жизни «на запрос», привязывает граф к HTTP и не
проверяет его до первого вызова.

## Решение

- [Dishka](https://dishka.readthedocs.io/) с двумя scope:
  - `Scope.APP`: `AsyncEngine`, `async_sessionmaker`, `Redis`, адаптеры Redis, хэшер паролей,
    сервис токенов, health probes, `CheckReadiness`;
  - `Scope.REQUEST`: `AsyncSession`, `AbstractUnitOfWork`, репозитории, все use cases и
    сервисы приложения (они делят одну сессию).
- Провайдеры разделены по контекстам: `ConfigProvider`, `DatabaseProvider`, `RedisProvider`,
  `SecurityProvider`, `UseCaseProvider`, `ObservabilityProvider`. Сборка выполняется в
  [ioc/container.py](../../src/app/ioc/container.py).
- Роуты используют `DishkaRoute` и `FromDishka[UseCase]`. Зависимости FastAPI (`CurrentUser`,
  rate limit) помечены `@inject` и берут объекты из того же контейнера запроса.
- Слои ниже `ioc` о Dishka не знают: use cases и адаптеры — обычные классы с конструкторами.
- Настройки передаются вниз плоскими объектами (`RefreshTokenSettings`, `HttpSettings`,
  `RateLimitPolicy`), а не целым `Settings`. Только `ioc` и `main` импортируют `app.config`.

## Альтернативы

- **FastAPI `Depends`.** Нет scope APP, граф не проверяется заранее, а use cases пришлось бы
  импортировать вместе с FastAPI.
- **dependency-injector.** Зрелая библиотека, но требует декларативных контейнеров-классов
  и хуже типизирована.
- **Ручная сборка в `main`.** Прозрачно, но с REQUEST-scope (новая сессия на запрос)
  приходится писать собственный мини-контейнер.

## Последствия

- Граф зависимостей проверяется при создании контейнера: забытый провайдер обнаруживается
  при старте или в первом же тесте, а не в проде.
- Тестам не нужны overrides: интеграционные тесты собирают тот же контейнер с другими
  `Settings`, например с URL Redis на закрытый порт для теста отказа.
- Ресурсы с `yield` (engine, Redis) закрываются при `container.close()` в lifespan.
