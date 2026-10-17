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
# Generated SDKs name their functions after operationId: these must not change.
PUSH_OPERATION_IDS = {
    "GET /api/v1/push/vapid-public-key": "getVapidPublicKey",
    "PUT /api/v1/push/subscriptions": "upsertPushSubscription",
    "DELETE /api/v1/push/subscriptions": "deletePushSubscription",
}
PROJECT_OPERATION_IDS = {
    "GET /api/v1/projects": "listProjects",
    "POST /api/v1/projects": "createProject",
    "GET /api/v1/projects/{project_id}": "getProject",
    "PATCH /api/v1/projects/{project_id}": "updateProject",
    "DELETE /api/v1/projects/{project_id}": "deleteProject",
    "PUT /api/v1/projects/{project_id}/reminders": "setProjectReminders",
    "GET /api/v1/projects/{project_id}/collaborators": "listCollaborators",
    "POST /api/v1/projects/{project_id}/collaborators": "addCollaborator",
    "DELETE /api/v1/projects/{project_id}/collaborators/{user_id}": "removeCollaborator",
}
PROTECTED = {
    *PROJECT_OPERATION_IDS,
    "PUT /api/v1/push/subscriptions",
    "DELETE /api/v1/push/subscriptions",
    "GET /api/v1/users/me",
    "DELETE /api/v1/users/me",
    "PUT /api/v1/users/me/password",
    "PUT /api/v1/users/me/username",
}
PUBLIC = {
    "GET /api/v1/push/vapid-public-key",
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


# Parameters a client really sends, besides the placeholders of the path itself.
DECLARED_PARAMETERS = {
    "POST /api/v1/projects": {("header", "idempotency-key")},
    "DELETE /api/v1/push/subscriptions": {("query", "endpoint")},
}


def test_injected_dependencies_do_not_leak_into_parameters(schema: Schema) -> None:
    """Use cases, rate limiter and settings come from Dishka; none of them may show
    up as a parameter a client would be asked to send."""
    leaked = {}
    for name, operation in operations(schema).items():
        path = name.split(" ", maxsplit=1)[1]
        unexpected = [
            (p["in"], p["name"])
            for p in operation.get("parameters", [])
            if not (p["in"] == "path" and f"{{{p['name']}}}" in path)
            and (p["in"], p["name"]) not in DECLARED_PARAMETERS.get(name, set())
        ]
        if unexpected:
            leaked[name] = unexpected

    assert leaked == {}


def test_operations_have_stable_ids(schema: Schema) -> None:
    expected = PROJECT_OPERATION_IDS | PUSH_OPERATION_IDS
    found = {name: operations(schema)[name].get("operationId") for name in expected}
    every_id = [op["operationId"] for op in operations(schema).values()]

    assert found == expected
    assert len(every_id) == len(set(every_id))


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
        ("DELETE /api/v1/users/me", "409"),
        ("POST /api/v1/projects", "409"),
        ("GET /api/v1/projects/{project_id}", "404"),
        ("PATCH /api/v1/projects/{project_id}", "404"),
        ("DELETE /api/v1/projects/{project_id}", "403"),
        ("DELETE /api/v1/projects/{project_id}", "404"),
        ("PUT /api/v1/projects/{project_id}/reminders", "404"),
        ("GET /api/v1/projects/{project_id}/collaborators", "404"),
        ("POST /api/v1/projects/{project_id}/collaborators", "200"),
        ("POST /api/v1/projects/{project_id}/collaborators", "404"),
        ("DELETE /api/v1/projects/{project_id}/collaborators/{user_id}", "404"),
        ("DELETE /api/v1/projects/{project_id}/collaborators/{user_id}", "409"),
        ("GET /health/ready", "503"),
    ],
)
def test_specific_error_statuses_are_documented(schema: Schema, name: str, status: str) -> None:
    assert status in operations(schema)[name]["responses"]


def _is_named(body: Schema) -> bool:
    """A named model, or an array of them (short lists are plain arrays)."""
    return "$ref" in body or (body.get("type") == "array" and "$ref" in body.get("items", {}))


def test_successful_responses_have_a_schema(schema: Schema) -> None:
    """Every 200/201 body is a named model: clients can generate types from it."""
    untyped = [
        f"{name} {status}"
        for name, operation in operations(schema).items()
        for status, response in operation["responses"].items()
        if status in {"200", "201"}
        and not _is_named(response.get("content", {}).get("application/json", {}).get("schema", {}))
    ]

    assert untyped == []


def test_error_response_schema_matches_the_contract(schema: Schema) -> None:
    body = schema["components"]["schemas"]["ErrorBody"]

    assert set(body["properties"]) == {"code", "message", "details", "request_id"}
    assert set(body["required"]) == {"code", "message"}


def test_title_and_version_come_from_the_settings(schema: Schema) -> None:
    assert (schema["info"]["title"], schema["info"]["version"]) == ("openapi-test", "9.9.9")


def test_patch_fields_that_cannot_be_cleared_are_not_nullable(schema: Schema) -> None:
    """Omitting a field keeps it; only the addresses accept null (clearing them)."""
    properties = schema["components"]["schemas"]["UpdateProjectRequest"]["properties"]

    nullable = {
        name
        for name, prop in properties.items()
        if any(option.get("type") == "null" for option in prop.get("anyOf", []))
    }

    assert nullable == {"origin_address", "destination_address"}
    assert "required" not in schema["components"]["schemas"]["UpdateProjectRequest"]
