"""Architecture rules checked on the source code (complements .importlinter).

import-linter guards layers and package independence. This test adds what it
cannot express:
- explicit allow-lists of what each domain and application module may import;
- "technology homes": each third-party technology is confined to its adapter;
- ORM models live in infrastructure/db/models only;
- every use case is a class with `async def __call__`;
- application services and helpers never commit (use cases own the transaction).
"""

import ast
import dataclasses
import importlib
import pkgutil
import sys
from pathlib import Path

import app
from app.domain.common.entity import Entity

SRC = Path(app.__file__).parent

# Domain module -> app packages it may import (besides itself).
DOMAIN_ALLOWED = {
    "common": frozenset[str](),
    "users": frozenset({"app.domain.common"}),
    "tokens": frozenset({"app.domain.common"}),
    "projects": frozenset({"app.domain.common"}),
    "inventory": frozenset({"app.domain.common"}),
    "tasks": frozenset({"app.domain.common"}),
    "notifications": frozenset({"app.domain.common"}),
    "audit": frozenset({"app.domain.common"}),
}
# Application feature -> other features it may use. Keep the graph acyclic.
APPLICATION_FEATURE_DEPS = {
    "users": frozenset[str](),
    "auth": frozenset({"users"}),
    "health": frozenset[str](),
    "projects": frozenset[str](),
    "notifications": frozenset[str](),
    "inventory": frozenset[str](),
    # Overview screens over the inventory: its access checks and reference views.
    "summaries": frozenset({"inventory"}),
}
APPLICATION_SHARED = ("app.application.common", "app.application.interfaces")
APPLICATION_SHARED_MODULES = ("app.application.exceptions",)
# Modules of a feature that are not use cases.
# read_model: a port of a feature (it returns the feature's DTOs, so it cannot live in
# application/interfaces, which must not import features).
NOT_USE_CASES = frozenset({"dto", "services", "lookup", "read_model", "__init__"})

# Top-level package -> source paths (relative to src/app) allowed to import it.
TECHNOLOGY_HOMES: dict[str, tuple[str, ...]] = {
    "sqlalchemy": ("infrastructure/db/", "ioc/db_provider.py"),
    "asyncpg": ("infrastructure/db/",),
    "redis": ("infrastructure/redis/", "infrastructure/tasks/", "ioc/redis_provider.py"),
    "argon2": ("infrastructure/security/",),
    "jwt": ("infrastructure/security/",),
    "prometheus_client": ("infrastructure/metrics/",),
    "structlog": ("infrastructure/", "presentation/"),
    "fastapi": ("presentation/", "main.py"),
    "starlette": ("presentation/",),
    "dishka": ("ioc/", "presentation/", "main.py", "worker/"),
    "taskiq": ("worker/", "infrastructure/tasks/"),
    "taskiq_redis": ("infrastructure/tasks/",),
    "aiohttp": ("infrastructure/push/", "infrastructure/llm/"),
    "pywebpush": ("infrastructure/push/",),
    "py_vapid": ("infrastructure/push/",),
    "cryptography": ("infrastructure/push/",),
    "pydantic": ("presentation/", "config.py"),
    "pydantic_settings": ("config.py",),
    "alembic": (),
    "uvicorn": (),
}
ORM_MODELS_HOME = "infrastructure/db/models/"


def imports_of(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _within(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def _top(name: str) -> str:
    return name.split(".", maxsplit=1)[0]


def _domain_violations(parts: tuple[str, ...], imports: set[str]) -> list[str]:
    module = parts[1] if len(parts) > 2 else None  # domain/<module>/<file>.py
    found = []
    for name in sorted(imports):
        if _top(name) != "app":
            if _top(name) not in sys.stdlib_module_names:
                found.append(f"domain must use the standard library only, imports {name}")
            continue
        if module is None:
            found.append(f"domain/{parts[-1]} must not import {name}")
            continue
        own = f"app.domain.{module}"
        allowed = DOMAIN_ALLOWED.get(module)
        if allowed is None:
            found.append(f"unknown domain module {module}: declare it in DOMAIN_ALLOWED")
        elif not _within(name, own) and not any(_within(name, a) for a in allowed):
            found.append(f"domain.{module} must not import {name}")
    return found


def _application_violations(parts: tuple[str, ...], imports: set[str]) -> list[str]:
    feature = parts[1] if len(parts) > 2 else None  # application/<feature>/<file>.py
    found = []
    for name in sorted(imports):
        if _top(name) != "app":
            if _top(name) not in sys.stdlib_module_names:
                found.append(f"application must use the standard library only, imports {name}")
            continue
        if not _within(name, "app.application"):
            if not _within(name, "app.domain"):
                found.append(f"application must not import {name}")
            continue
        if feature is None or feature in {"common", "interfaces"}:
            # Shared parts must not depend on features.
            if not name.startswith(APPLICATION_SHARED) and name not in APPLICATION_SHARED_MODULES:
                found.append(f"application/{'/'.join(parts[1:])} must not import {name}")
            continue
        deps = APPLICATION_FEATURE_DEPS.get(feature)
        if deps is None:
            found.append(f"unknown feature {feature}: declare it in APPLICATION_FEATURE_DEPS")
            continue
        allowed = (
            f"app.application.{feature}",
            *APPLICATION_SHARED,
            *APPLICATION_SHARED_MODULES,
            *(f"app.application.{dep}" for dep in deps),
        )
        if not any(_within(name, prefix) for prefix in allowed):
            found.append(f"application.{feature} must not import {name}")
    return found


def _technology_violations(rel_path: str, imports: set[str]) -> list[str]:
    found = []
    for name in sorted(imports):
        homes = TECHNOLOGY_HOMES.get(_top(name))
        if homes is not None and not rel_path.startswith(homes):
            where = ", ".join(homes) or "nowhere in src/app"
            found.append(f"{_top(name)} may only be imported in {where}, not {rel_path}")
    return found


def _use_case_violations(parts: tuple[str, ...], tree: ast.Module) -> list[str]:
    is_feature_module = (
        len(parts) == 3  # application/<feature>/<module>.py
        and parts[0] == "application"
        and parts[1] in APPLICATION_FEATURE_DEPS
        and Path(parts[2]).stem not in NOT_USE_CASES
    )
    if not is_feature_module:
        return []
    found = []
    public_classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    ]
    for cls in public_classes:
        has_async_call = any(
            isinstance(item, ast.AsyncFunctionDef) and item.name == "__call__" for item in cls.body
        )
        if not has_async_call:
            found.append(f"use case {cls.name} must define `async def __call__`")
    return found


def _commit_violations(parts: tuple[str, ...], tree: ast.Module) -> list[str]:
    """Services, helpers and shared code stage changes; only use cases commit."""
    stem = Path(parts[-1]).stem
    is_helper = parts[0] == "application" and (
        stem in {"services", "lookup"} or (len(parts) > 1 and parts[1] == "common")
    )
    if not is_helper or stem == "uow":
        return []
    return [
        f"{'/'.join(parts)} must not commit: the calling use case owns the transaction"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "commit"
    ]


def _orm_model_violations(rel_path: str, tree: ast.Module) -> list[str]:
    defines_model = any(
        isinstance(node, ast.ClassDef)
        and any(isinstance(base, ast.Name) and base.id == "Base" for base in node.bases)
        for node in ast.walk(tree)
    )
    if defines_model and not rel_path.startswith(ORM_MODELS_HOME):
        return [f"ORM models must live in {ORM_MODELS_HOME}, found in {rel_path}"]
    return []


def violations(rel_path: str, source: str) -> list[str]:
    """Rule violations of one file; rel_path is relative to src/app."""
    parts = Path(rel_path).parts
    tree = ast.parse(source)
    imports = imports_of(source)
    found = _technology_violations(rel_path, imports)
    found += _orm_model_violations(rel_path, tree)
    if parts[0] == "domain":
        found += _domain_violations(parts, imports)
    if parts[0] == "application":
        found += _application_violations(parts, imports)
        found += _use_case_violations(parts, tree)
        found += _commit_violations(parts, tree)
    return found


def source_files() -> list[Path]:
    return sorted(path for path in SRC.rglob("*.py") if "__pycache__" not in path.parts)


def test_codebase_follows_the_rules() -> None:
    found = [
        f"{path.relative_to(SRC)}: {problem}"
        for path in source_files()
        for problem in violations(path.relative_to(SRC).as_posix(), path.read_text())
    ]
    assert not found, "Architecture rules broken:\n" + "\n".join(found)


def test_rules_are_not_vacuous() -> None:
    """Every module the rules talk about exists, and nothing exists unregistered."""
    domain = {p.parent.name for p in (SRC / "domain").glob("*/__init__.py")}
    features = {p.parent.name for p in (SRC / "application").glob("*/__init__.py")}

    assert domain == set(DOMAIN_ALLOWED)
    assert features - {"common", "interfaces"} == set(APPLICATION_FEATURE_DEPS)
    assert len(source_files()) > 50


def test_feature_graph_is_acyclic() -> None:
    def visit(feature: str, path: tuple[str, ...]) -> None:
        assert feature not in path, f"cycle: {' -> '.join((*path, feature))}"
        for dep in APPLICATION_FEATURE_DEPS[feature]:
            visit(dep, (*path, feature))

    for feature in APPLICATION_FEATURE_DEPS:
        visit(feature, ())


def test_entities_are_slotted_keyword_only_dataclasses_with_identity_equality() -> None:
    import app.domain  # noqa: PLC0415

    for module in pkgutil.walk_packages(app.domain.__path__, "app.domain."):
        importlib.import_module(module.name)  # register Entity subclasses

    entities = Entity.__subclasses__()
    assert {cls.__name__ for cls in entities} >= {
        "User",
        "RefreshToken",
        "Project",
        "Room",
        "Box",
        "Item",
        "Task",
        "Notification",
        "PushSubscription",
        "AuditEntry",
    }
    for cls in entities:
        params = cls.__dataclass_params__  # type: ignore[attr-defined]
        assert hasattr(cls, "__slots__"), cls
        assert params.eq is False, f"{cls.__name__}: equality must come from Entity (by id)"
        assert all(f.kw_only for f in dataclasses.fields(cls)), cls
        assert cls.__eq__ is Entity.__eq__, cls
        assert cls.__hash__ is Entity.__hash__, cls


# (file, source, expected problem): the rules must fire on real violations.
VIOLATIONS = [
    ("domain/tokens/entity.py", "import sqlalchemy", "sqlalchemy may only be imported"),
    ("domain/tokens/entity.py", "import structlog", "standard library only"),
    ("domain/tokens/entity.py", "from app.domain.users.entity import User", "must not import"),
    ("domain/tokens/entity.py", "from app.application.exceptions import X", "must not import"),
    ("domain/common/ids.py", "from app.domain.users import x", "must not import"),
    ("application/users/get_user.py", "from app.application.auth import x", "must not"),
    ("application/users/get_user.py", "from app.infrastructure.db import x", "must not"),
    ("application/users/get_user.py", "from pydantic import BaseModel", "may only be"),
    ("application/common/clock.py", "from app.application.users import dto", "must not"),
    (
        "application/users/do_things.py",
        "class DoThings:\n    def __call__(self) -> None: ...",
        "must define `async def __call__`",
    ),
    (
        "application/auth/services.py",
        "async def issue(uow):\n    await uow.commit()",
        "must not commit",
    ),
    ("presentation/http/routes/x.py", "from sqlalchemy import select", "may only be"),
    ("presentation/http/routes/x.py", "import argon2", "may only be"),
    ("infrastructure/redis/rate_limit.py", "from sqlalchemy import select", "may only be"),
    ("infrastructure/db/repositories/x.py", "import redis", "may only be"),
    ("infrastructure/db/repositories/x.py", "class Foo(Base): pass", "ORM models must"),
    ("ioc/container.py", "import fastapi", "fastapi may only be"),
]


def test_checker_detects_violations() -> None:
    missed = [
        (rel_path, source)
        for rel_path, source, expected in VIOLATIONS
        if not any(expected in problem for problem in violations(rel_path, source))
    ]

    assert missed == []


def test_checker_accepts_allowed_imports() -> None:
    assert violations("application/auth/login.py", "from app.application.users import dto") == []
    assert violations("domain/tokens/entity.py", "from app.domain.common.ids import new_id") == []
