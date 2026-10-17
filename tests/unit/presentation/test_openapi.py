"""The published contract (/openapi.json): what clients and generated SDKs rely on.

FastAPI builds the schema without touching the database, so no Docker is needed.
"""

from typing import Any

import pytest
from dishka.integrations.fastapi import FastapiProvider

from app.application.inventory.scan_items import PHOTO_MEDIA_TYPES
from app.config import Settings
from app.domain.inventory.box import BoxStatus
from app.domain.inventory.item import ItemState
from app.domain.tasks.value_objects import TaskPhase
from app.ioc.config_provider import http_settings_from
from app.ioc.container import create_container
from app.presentation.http.app import create_app
from app.presentation.http.routes.summaries import MAX_LABELS_PER_REQUEST
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
SUMMARY_OPERATION_IDS = {
    "GET /api/v1/projects/{project_id}/summary": "getProjectSummary",
    "GET /api/v1/projects/{project_id}/loading-checklist": "getLoadingChecklist",
    "GET /api/v1/projects/{project_id}/unpacking-progress": "getUnpackingProgress",
    "GET /api/v1/projects/{project_id}/labels": "getBoxLabels",
}
TASK_OPERATION_IDS = {
    "GET /api/v1/task-templates": "listTaskTemplates",
    "POST /api/v1/projects/{project_id}/tasks/from-templates": "createTasksFromTemplates",
    "GET /api/v1/projects/{project_id}/tasks": "listTasks",
    "POST /api/v1/projects/{project_id}/tasks": "createTask",
    "GET /api/v1/tasks/{task_id}": "getTask",
    "PATCH /api/v1/tasks/{task_id}": "updateTask",
    "POST /api/v1/tasks/{task_id}/complete": "completeTask",
    "POST /api/v1/tasks/{task_id}/reopen": "reopenTask",
    "PUT /api/v1/tasks/{task_id}/assignees": "setTaskAssignees",
    "PUT /api/v1/tasks/{task_id}/reminders": "setTaskReminders",
    "DELETE /api/v1/tasks/{task_id}": "deleteTask",
}
PROTECTED = {
    *PROJECT_OPERATION_IDS,
    *SUMMARY_OPERATION_IDS,
    *TASK_OPERATION_IDS,
    "PUT /api/v1/push/subscriptions",
    "DELETE /api/v1/push/subscriptions",
    "GET /api/v1/users/me",
    "DELETE /api/v1/users/me",
    "PUT /api/v1/users/me/password",
    "PUT /api/v1/users/me/username",
    # inventory (api-endpoints.md, sections 5-7)
    "GET /api/v1/projects/{project_id}/rooms",
    "POST /api/v1/projects/{project_id}/rooms",
    "PATCH /api/v1/rooms/{room_id}",
    "DELETE /api/v1/rooms/{room_id}",
    "GET /api/v1/projects/{project_id}/boxes",
    "POST /api/v1/projects/{project_id}/boxes",
    "GET /api/v1/boxes/{box_id}",
    "PATCH /api/v1/boxes/{box_id}",
    "PUT /api/v1/boxes/{box_id}/status",
    "DELETE /api/v1/boxes/{box_id}",
    "PUT /api/v1/boxes/{box_id}/contents-reminder",
    "DELETE /api/v1/boxes/{box_id}/contents-reminder",
    "GET /api/v1/projects/{project_id}/items",
    "POST /api/v1/projects/{project_id}/items",
    "POST /api/v1/projects/{project_id}/items/batch",
    "POST /api/v1/projects/{project_id}/items/scan",
    "GET /api/v1/projects/{project_id}/items/search",
    "GET /api/v1/items/{item_id}",
    "PATCH /api/v1/items/{item_id}",
    "PUT /api/v1/items/{item_id}/state",
    "DELETE /api/v1/items/{item_id}",
    "GET /api/v1/items/{item_id}/suggestions",
}
# A token is optional: who the caller is decides the answer (a box card opened by QR).
OPTIONAL_AUTH = {
    "GET /api/v1/boxes/by-qr/{qr_token}",
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

INVENTORY_OPERATION_IDS = {
    "GET /api/v1/projects/{project_id}/rooms": "listRooms",
    "POST /api/v1/projects/{project_id}/rooms": "createRoom",
    "PATCH /api/v1/rooms/{room_id}": "updateRoom",
    "DELETE /api/v1/rooms/{room_id}": "deleteRoom",
    "GET /api/v1/projects/{project_id}/boxes": "listBoxes",
    "POST /api/v1/projects/{project_id}/boxes": "createBox",
    "GET /api/v1/boxes/{box_id}": "getBox",
    "PATCH /api/v1/boxes/{box_id}": "updateBox",
    "PUT /api/v1/boxes/{box_id}/status": "setBoxStatus",
    "DELETE /api/v1/boxes/{box_id}": "deleteBox",
    "PUT /api/v1/boxes/{box_id}/contents-reminder": "setBoxContentsReminder",
    "DELETE /api/v1/boxes/{box_id}/contents-reminder": "cancelBoxContentsReminder",
    "GET /api/v1/boxes/by-qr/{qr_token}": "getBoxByQr",
    "GET /api/v1/projects/{project_id}/items": "listItems",
    "POST /api/v1/projects/{project_id}/items": "createItem",
    "POST /api/v1/projects/{project_id}/items/batch": "createItems",
    "POST /api/v1/projects/{project_id}/items/scan": "scanItems",
    "GET /api/v1/projects/{project_id}/items/search": "searchItems",
    "GET /api/v1/items/{item_id}": "getItem",
    "PATCH /api/v1/items/{item_id}": "updateItem",
    "PUT /api/v1/items/{item_id}/state": "setItemState",
    "DELETE /api/v1/items/{item_id}": "deleteItem",
    "GET /api/v1/items/{item_id}/suggestions": "getItemSuggestions",
}


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
    assert set(operations(schema)) == PROTECTED | PUBLIC | OPTIONAL_AUTH
    assert not PROTECTED & PUBLIC
    assert not (PROTECTED | PUBLIC) & OPTIONAL_AUTH


def _error_body(operation: Schema, status: str) -> Schema | None:
    response = operation["responses"].get(status, {})
    body: Schema | None = response.get("content", {}).get("application/json", {}).get("schema")
    return body


def test_authentication_is_declared_as_classified(schema: Schema) -> None:
    """Protected operations require the bearer token and document 401; optional ones
    accept it and also document 404; public ones do not ask for it."""
    bearer: list[dict[str, list[str]]] = [{"HTTPBearer": []}]
    wrong = [
        name
        for name, operation in operations(schema).items()
        if (
            (name in PROTECTED or name in OPTIONAL_AUTH)
            and (operation.get("security") != bearer or _error_body(operation, "401") is None)
        )
        or (name in OPTIONAL_AUTH and "404" not in operation["responses"])
        or (name in PUBLIC and "security" in operation)
    ]

    assert wrong == []
    assert all(
        _error_body(operations(schema)[name], "401") == {"$ref": ERROR_REF} for name in PROTECTED
    )


def test_bearer_scheme_tells_where_tokens_come_from(schema: Schema) -> None:
    bearer = schema["components"]["securitySchemes"]["HTTPBearer"]

    assert (bearer["type"], bearer["scheme"]) == ("http", "bearer")
    assert "/api/v1/auth/login" in bearer["description"]


# Parameters a client really sends, besides the placeholders of the path itself.
DECLARED_PARAMETERS = {
    "POST /api/v1/projects": {("header", "idempotency-key")},
    "DELETE /api/v1/push/subscriptions": {("query", "endpoint")},
    "GET /api/v1/projects/{project_id}/rooms": {("query", "kind")},
    "GET /api/v1/projects/{project_id}/boxes": {
        ("query", "status"),
        ("query", "destination_room_id"),
        ("query", "number"),
        ("query", "limit"),
        ("query", "after"),
    },
    "GET /api/v1/projects/{project_id}/items": {
        ("query", "state"),
        ("query", "priority"),
        ("query", "box_id"),
        ("query", "source_room_id"),
        ("query", "limit"),
        ("query", "after"),
    },
    "GET /api/v1/projects/{project_id}/items/search": {
        ("query", "q"),
        ("query", "limit"),
        ("query", "offset"),
    },
    "GET /api/v1/projects/{project_id}/labels": {("query", "box_ids")},
    "GET /api/v1/projects/{project_id}/tasks": {("query", "phase"), ("query", "is_done")},
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
    expected = (
        PROJECT_OPERATION_IDS
        | PUSH_OPERATION_IDS
        | INVENTORY_OPERATION_IDS
        | SUMMARY_OPERATION_IDS
        | TASK_OPERATION_IDS
    )
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


def test_versioned_operations_document_the_error_contract(schema: Schema) -> None:
    undocumented = [
        f"{name} {status}"
        for name, operation in operations(schema).items()
        if "/api/v1/" in name
        for status in ("422", "429", "500")
        if _error_body(operation, status) != {"$ref": ERROR_REF}
    ]

    assert undocumented == []


# Error statuses of specific operations that clients branch on.
SPECIFIC_ERRORS = [
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
    ("POST /api/v1/projects/{project_id}/rooms", "409"),
    ("DELETE /api/v1/rooms/{room_id}", "409"),
    ("PUT /api/v1/boxes/{box_id}/status", "409"),
    ("PUT /api/v1/boxes/{box_id}/contents-reminder", "403"),
    ("PUT /api/v1/boxes/{box_id}/contents-reminder", "409"),
    ("PUT /api/v1/items/{item_id}/state", "409"),
    ("GET /api/v1/items/{item_id}", "404"),
    ("POST /api/v1/projects/{project_id}/items/scan", "404"),
    ("POST /api/v1/projects/{project_id}/items/scan", "413"),
    ("POST /api/v1/projects/{project_id}/items/scan", "415"),
    ("POST /api/v1/projects/{project_id}/items/scan", "503"),
    *((name, "404") for name in SUMMARY_OPERATION_IDS),
    *((name, "404") for name in TASK_OPERATION_IDS if "task-templates" not in name),
    ("POST /api/v1/projects/{project_id}/tasks", "409"),
    ("PUT /api/v1/tasks/{task_id}/reminders", "409"),
]


def test_specific_error_statuses_are_documented(schema: Schema) -> None:
    missing = [
        (name, status)
        for name, status in SPECIFIC_ERRORS
        if status not in operations(schema)[name]["responses"]
    ]

    assert missing == []


def _is_named(body: Schema) -> bool:
    """A named model, or an array of them (short lists are plain arrays)."""
    return "$ref" in body or (body.get("type") == "array" and "$ref" in body.get("items", {}))


def test_successful_responses_have_a_schema(schema: Schema) -> None:
    """Every 200/201 body is a named model (or a list of them): clients can generate
    types from it."""
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


@pytest.mark.parametrize(
    ("model", "enum"),
    [
        ("BoxStatusCounts", BoxStatus),
        ("ItemStateCounts", ItemState),
        ("TaskPhaseCounts", TaskPhase),
    ],
)
def test_counters_have_a_required_field_per_enum_value(
    schema: Schema, model: str, enum: type[BoxStatus | ItemState | TaskPhase]
) -> None:
    """Exact objects instead of maps: a new status must show up in the contract."""
    counts = schema["components"]["schemas"][model]

    assert list(counts["properties"]) == [member.value for member in enum]
    assert counts["required"] == list(counts["properties"])


def test_labels_limit_the_number_of_chosen_boxes(schema: Schema) -> None:
    [box_ids] = [
        p
        for p in operations(schema)["GET /api/v1/projects/{project_id}/labels"]["parameters"]
        if p["name"] == "box_ids"
    ]
    array = next(option for option in box_ids["schema"]["anyOf"] if option["type"] == "array")

    assert array["maxItems"] == MAX_LABELS_PER_REQUEST
    assert box_ids["required"] is False


def test_a_scan_takes_the_image_itself_as_the_body(schema: Schema) -> None:
    """No multipart and no base64: `fetch(url, {body: file})` with the file's type."""
    operation = operations(schema)["POST /api/v1/projects/{project_id}/items/scan"]
    body = operation["requestBody"]
    binary = {"type": "string", "format": "binary"}

    assert body["required"] is True
    assert set(body["content"]) == PHOTO_MEDIA_TYPES
    assert all(media["schema"] == binary for media in body["content"].values())
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ItemScan"
    }
