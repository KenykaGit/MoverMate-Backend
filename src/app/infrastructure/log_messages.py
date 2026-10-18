"""Human-readable `message` for every known log event.

Events stay machine-friendly (`event` plus flat fields, as logged); this module adds
one sentence such as `user alice created project "Move to Berlin"` built from those
fields. Sage shows `message` in its result list, so a log search reads like a story
of what users did. Formatting never raises: a missing field renders as `?`.

Who did it (`{actor}`) is the username bound by the auth dependency or passed by the
use case, else the `user_id`, else `anonymous`. Event fields win over the context.
"""

from collections.abc import Callable, Mapping, MutableMapping
from typing import Any

_MISSING = "?"

type Template = str | Callable[[Mapping[str, Any]], str]


def _http_request(fields: Mapping[str, Any]) -> str:
    code = fields.get("error_code")
    outcome = f"{fields.get('status', _MISSING)} {code}" if code else fields.get("status", _MISSING)
    return (
        f"{actor(fields)}: {fields.get('method', _MISSING)} {fields.get('path', _MISSING)}"
        f" -> {outcome} in {fields.get('duration_ms', _MISSING)} ms"
    )


EVENT_MESSAGES: Mapping[str, Template] = {
    # HTTP and process lifecycle
    "http_request": _http_request,
    "unhandled_error": "Unhandled error on {method} {path}",
    "startup": "API {version} started",
    "shutdown": "API stopped",
    "redis_unavailable": "Redis operation {op} failed, continuing without Redis",
    "readiness_check_failed": "Readiness check {dependency} failed: {error}",
    "idempotency_record_corrupted": "Idempotency record {key} is unreadable",
    "idempotent_replay": "{actor} repeated a request ({scope}): the stored response is replayed",
    # Accounts and sessions
    "user_registered": "{actor} registered",
    "user_logged_in": "{actor} logged in",
    "user_logged_out": "{actor} logged out of session {family_id}",
    "login_failed": "Login failed: {reason}",
    "password_rehashed": "Password hash of {actor} upgraded to the current parameters",
    "refresh_token_reused": (
        "Refresh token of {actor} was used twice: session {family_id} revoked"
    ),
    "password_changed": "{actor} changed the password, all sessions revoked",
    "username_changed": "User {previous_username} renamed to {username}",
    "user_deleted": "{actor} deleted the account",
    "user_restored": "{actor} restored the account",
    "test_users_seeded": "Test users seeded: created {created}; skipped {skipped}",
    "test_users_seed_failed": "Seeding test users failed",
    # Projects
    "project_created": '{actor} created project "{project_name}" with {rooms} rooms',
    "project_updated": '{actor} updated project "{project_name}": {fields}',
    "project_rescheduled": 'Project "{project_name}" moves on {moving_date}: reminders moved',
    "project_reminders_changed": (
        '{actor} set moving reminders of project "{project_name}" to {offsets_days} days before'
    ),
    "project_deleted": (
        '{actor} deleted project "{project_name}",'
        " {cancelled_notifications} notifications cancelled"
    ),
    "project_delete_forbidden": '{actor} tried to delete project "{project_name}" they do not own',
    "collaborator_added": '{actor} added {collaborator_username} to project "{project_name}"',
    "collaborator_removed": '{actor} removed user {collaborator_id} from project "{project_name}"',
    "project_left": '{actor} left project "{project_name}"',
    # Rooms, boxes, items
    "room_created": '{actor} created {room_kind} room "{room_name}"',
    "room_renamed": '{actor} renamed room "{previous_name}" to "{room_name}"',
    "room_deleted": '{actor} deleted room "{room_name}"',
    "box_created": "{actor} created box #{number} with {items} items",
    "box_updated": "{actor} updated box #{number}: {fields}",
    "box_status_changed": "{actor} moved box #{number} from {from_status} to {to_status}",
    "box_deleted": "{actor} deleted box #{number} with {items} items",
    "contents_reminder_scheduled": (
        "{actor} will be reminded to fill in box #{number} at {remind_at}"
    ),
    "contents_reminder_cancelled": "{actor} cancelled the contents reminder of box #{number}",
    "item_created": '{actor} added item "{item_name}" x{quantity}',
    "items_created": "{actor} added {items} items: {item_names}",
    "item_updated": '{actor} updated item "{item_name}": {fields}',
    "item_deleted": '{actor} deleted item "{item_name}"',
    "item_state_changed": '{actor} changed item "{item_name}" from {from_state} to {to_state}',
    "items_scanned": "{actor} scanned a photo: {recognized} recognized, {items} items suggested",
    "llm_request_completed": "Model {model} did {operation} in {duration_seconds} s",
    "llm_request_failed": "Model {model} failed {operation}: {outcome}",
    # Tasks
    "task_created": '{actor} created task "{task_title}" with {assignees} assignees',
    "task_updated": '{actor} updated task "{task_title}": {fields}',
    "tasks_created_from_templates": "{actor} created {tasks} tasks from templates",
    "task_completed": '{actor} completed task "{task_title}"',
    "task_reopened": '{actor} reopened task "{task_title}"',
    "task_assignees_changed": (
        '{actor} changed assignees of task "{task_title}": +{added_count} -{removed_count}'
    ),
    "task_reminders_changed": (
        '{actor} set reminders of task "{task_title}" to {offsets_hours} hours before'
    ),
    "task_deleted": '{actor} deleted task "{task_title}"',
    # Push notifications and the worker
    "push_subscribed": "{actor} subscribed a browser to push notifications ({push_service})",
    "push_unsubscribed": "{actor} unsubscribed a browser from push notifications",
    "push_subscription_gone": "Push subscription of user {user_id} is gone: {reason}",
    "worker_started": "Worker {version} started, polling every {interval} s",
    "notifications_drained": "Worker processed {count} notifications",
    "notification_processed": (
        "{notification_type} notification for user {user_id}: {outcome} (attempt {attempts})"
    ),
    "worker_iteration_failed": "Worker iteration failed",
    "worker_stopped": "Worker stopped",
}


def actor(fields: Mapping[str, Any]) -> str:
    """Who did it: `user alice`, `user <id>` before the name is known, or `anonymous`."""
    who = fields.get("username") or fields.get("user_id")
    return f"user {who}" if who else "anonymous"


class _Fields(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return _MISSING


def _text(value: Any) -> Any:
    if isinstance(value, list | tuple | set | frozenset):
        return ", ".join(str(part) for part in value) or "nothing"
    return value


def render_message(event: str, fields: Mapping[str, Any]) -> str | None:
    """The sentence for a known event; None for anything else (uvicorn, taskiq...)."""
    template = EVENT_MESSAGES.get(event)
    if template is None:
        return None
    if callable(template):
        return template(fields)
    values = _Fields({key: _text(value) for key, value in fields.items()})
    values["actor"] = actor(fields)
    return template.format_map(values)


def add_message(
    _logger: object, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: puts the sentence into `message` unless one is set."""
    event = event_dict.get("event")
    if "message" in event_dict or not isinstance(event, str):
        return event_dict
    try:
        message = render_message(event, event_dict)
    except Exception:  # noqa: BLE001  # a broken template must never lose the event
        return event_dict
    if message is not None:
        event_dict["message"] = message
    return event_dict
