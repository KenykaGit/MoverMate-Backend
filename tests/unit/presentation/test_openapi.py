"""The published contract (/openapi.json): what clients and generated SDKs rely on.

FastAPI builds the schema without touching the database, so no Docker is needed.
"""

from typing import Any

import pytest
from dishka.integrations.fastapi import FastapiProvider

from app.config import Settings
from app.ioc.config_provider import http_settings_from
from app.ioc.container import create_container
from app.presentation.http.app import create_app
from tests.routes import ECHO_PATH, ITEM_PATH, add_test_routes

type Schema = dict[str, Any]

# Every operation is classified: a new endpoint fails test_every_operation_is_classified
# until someone decides whether it needs a user.
PROTECTED = {
    "GET /api/v1/users/me",
    "DELETE /api/v1/users/me",
    "PUT /api/v1/users/me/password",
    "PUT /api/v1/users/me/username",
}
PUBLIC = {
    "POST /api/v1/auth/register",
    "POST /api/v1/auth/login",
    "POST /api/v1/auth/refresh",
    "POST /api/v1/auth/logout",
    "GET /health/live",
    "GET /health/ready",
}
ERROR_REF = "#/components/schemas/ErrorResponse"


def _build(*, with_test_routes: bool = False) -> Schema:
    settings = Settings(_env_file=None, app_name="openapi-test", app_version="9.9.9")
    app = create_app(create_container(settings, FastapiProvider()), http_settings_from(settings))
    if with_test_routes:
        add_test_routes(app)
    return app.openapi()


@pytest.fixture(scope="module")
def schema() -> Schema:
    return _build()


def operations(schema: Schema) -> dict[str, Schema]:
    return {
        f"{method.upper()} {path}": operation
        for path, item in schema["paths"].items()
        for method, operation in item.items()
    }


def test_every_operation_is_classified(schema: Schema) -> None:
    assert set(operations(schema)) == PROTECTED | PUBLIC
    assert not PROTECTED & PUBLIC


@pytest.mark.parametrize("name", sorted(PROTECTED))
def test_protected_operations_declare_bearer_auth_and_401(schema: Schema, name: str) -> None:
    operation = operations(schema)[name]

    assert operation.get("security") == [{"HTTPBearer": []}]
    assert operation["responses"]["401"]["content"]["application/json"]["schema"] == {
        "$ref": ERROR_REF
    }


@pytest.mark.parametrize("name", sorted(PUBLIC))
def test_public_operations_do_not_require_auth(schema: Schema, name: str) -> None:
    assert "security" not in operations(schema)[name]


def test_bearer_scheme_tells_where_tokens_come_from(schema: Schema) -> None:
    bearer = schema["components"]["securitySchemes"]["HTTPBearer"]

    assert (bearer["type"], bearer["scheme"]) == ("http", "bearer")
    assert "/api/v1/auth/login" in bearer["description"]


def test_injected_dependencies_do_not_leak_into_parameters(schema: Schema) -> None:
    """Use cases, rate limiter and settings come from Dishka; none of them may show
    up as a query parameter a client would be asked to send."""
    leaked = {
        name: op["parameters"] for name, op in operations(schema).items() if "parameters" in op
    }

    assert leaked == {}


def test_only_real_parameters_are_published_with_injection() -> None:
    """A route mixing Dishka, a rate-limit dependency, a header and a path parameter."""
    published = {
        name: [(p["in"], p["name"], p["required"]) for p in op.get("parameters", [])]
        for name, op in operations(_build(with_test_routes=True)).items()
        if "/test/" in name
    }

    assert published == {
        f"POST {ECHO_PATH}": [("header", "idempotency-key", False)],
        f"GET {ITEM_PATH}/{{item_id}}": [("path", "item_id", True)],
    }


@pytest.mark.parametrize("name", sorted(op for op in PROTECTED | PUBLIC if "/api/v1/" in op))
def test_versioned_operations_document_the_error_contract(schema: Schema, name: str) -> None:
    responses = operations(schema)[name]["responses"]

    for status in ("422", "429", "500"):
        assert responses[status]["content"]["application/json"]["schema"] == {"$ref": ERROR_REF}


@pytest.mark.parametrize(
    ("name", "status"),
    [
        ("POST /api/v1/auth/register", "409"),
        ("POST /api/v1/auth/login", "401"),
        ("POST /api/v1/auth/refresh", "401"),
        ("PUT /api/v1/users/me/username", "409"),
        ("GET /health/ready", "503"),
    ],
)
def test_specific_error_statuses_are_documented(schema: Schema, name: str, status: str) -> None:
    assert status in operations(schema)[name]["responses"]


def test_successful_responses_have_a_schema(schema: Schema) -> None:
    """Every 200/201 body is a named model: clients can generate types from it."""
    untyped = [
        f"{name} {status}"
        for name, operation in operations(schema).items()
        for status, response in operation["responses"].items()
        if status in {"200", "201"}
        and "$ref" not in response.get("content", {}).get("application/json", {}).get("schema", {})
    ]

    assert untyped == []


def test_error_response_schema_matches_the_contract(schema: Schema) -> None:
    body = schema["components"]["schemas"]["ErrorBody"]

    assert set(body["properties"]) == {"code", "message", "details", "request_id"}
    assert set(body["required"]) == {"code", "message"}


def test_title_and_version_come_from_the_settings(schema: Schema) -> None:
    assert (schema["info"]["title"], schema["info"]["version"]) == ("openapi-test", "9.9.9")
