"""The single error contract: every error leaves the service as ErrorResponse.

{"error": {"code": "...", "message": "...", "details": [...], "request_id": "..."}}
"""

from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.application.exceptions import (
    ApplicationError,
    BoxNotFoundError,
    BoxNotPackingError,
    BoxStatusConflictError,
    IdempotencyKeyReusedError,
    InvalidCredentialsError,
    InvalidPaginationError,
    InvalidRefreshTokenError,
    ItemBoxMismatchError,
    ItemNotFoundError,
    NotAuthenticatedError,
    NotBoxCreatorError,
    NotFoundError,
    ProjectNotFoundError,
    ReminderInThePastError,
    RequestInProgressError,
    RoomInUseError,
    RoomNameTakenError,
    RoomNotFoundError,
    UsernameAlreadyTakenError,
    UserNotFoundError,
    UserOwnsProjectsError,
)
from app.domain.common.errors import (
    DomainError,
    InvalidMovingReminderOffsetError,
    InvalidReminderOffsetError,
)
from app.domain.inventory.errors import (
    BoxStatusTransitionError,
    CrossProjectReferenceError,
    InvalidBoxNumberError,
    InvalidBoxTitleError,
    InvalidItemNameError,
    InvalidQrTokenError,
    InvalidQuantityError,
    InvalidRoomNameError,
    ItemStateTransitionError,
    WrongRoomKindError,
)
from app.domain.notifications.errors import (
    InvalidPushSubscriptionError,
    NotificationAlreadyFinishedError,
)
from app.domain.projects.errors import (
    CannotRemoveOwnerError,
    InvalidAddressError,
    InvalidProjectNameError,
    InvalidTimeZoneError,
    NotProjectOwnerError,
)
from app.domain.tasks.errors import (
    InvalidDueDateError,
    InvalidTaskDescriptionError,
    InvalidTaskTitleError,
    ReminderWithoutDueDateError,
)
from app.domain.users.errors import (
    InvalidPasswordError,
    InvalidPasswordHashError,
    InvalidUsernameError,
)
from app.presentation.http.schemas.common import ErrorBody, ErrorDetail, ErrorResponse

logger = structlog.get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
_UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    status_code: int
    code: str
    """Stable machine-readable id: clients branch on it, never on messages."""


# The most specific class in an error's MRO wins. An ApplicationError that is
# not covered here is a bug and becomes a 500, not a guessed status code.
ERROR_SPECS: dict[type[Exception], ErrorSpec] = {
    # domain rules
    DomainError: ErrorSpec(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_value"),
    InvalidUsernameError: ErrorSpec(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_username"),
    InvalidPasswordError: ErrorSpec(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_password"),
    InvalidReminderOffsetError: ErrorSpec(
        status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_reminder_offset"
    ),
    InvalidProjectNameError: ErrorSpec(
        status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_project_name"
    ),
    InvalidAddressError: ErrorSpec(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_address"),
    InvalidTimeZoneError: ErrorSpec(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_time_zone"),
    InvalidMovingReminderOffsetError: ErrorSpec(
        status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_moving_reminder_offset"
    ),
    CannotRemoveOwnerError: ErrorSpec(status.HTTP_409_CONFLICT, "cannot_remove_owner"),
    NotProjectOwnerError: ErrorSpec(status.HTTP_403_FORBIDDEN, "not_project_owner"),
    InvalidRoomNameError: ErrorSpec(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_room_name"),
    InvalidBoxNumberError: ErrorSpec(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_box_number"),
    InvalidBoxTitleError: ErrorSpec(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_box_title"),
    InvalidQrTokenError: ErrorSpec(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_qr_token"),
    InvalidItemNameError: ErrorSpec(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_item_name"),
    InvalidQuantityError: ErrorSpec(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_quantity"),
    WrongRoomKindError: ErrorSpec(status.HTTP_422_UNPROCESSABLE_CONTENT, "wrong_room_kind"),
    CrossProjectReferenceError: ErrorSpec(
        status.HTTP_422_UNPROCESSABLE_CONTENT, "cross_project_reference"
    ),
    BoxStatusTransitionError: ErrorSpec(status.HTTP_409_CONFLICT, "invalid_box_transition"),
    ItemStateTransitionError: ErrorSpec(status.HTTP_409_CONFLICT, "invalid_item_transition"),
    InvalidTaskTitleError: ErrorSpec(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_task_title"),
    InvalidTaskDescriptionError: ErrorSpec(
        status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_task_description"
    ),
    InvalidDueDateError: ErrorSpec(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_due_date"),
    ReminderWithoutDueDateError: ErrorSpec(status.HTTP_409_CONFLICT, "reminder_without_due_date"),
    InvalidPushSubscriptionError: ErrorSpec(
        status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_push_subscription"
    ),
    # application outcomes
    NotFoundError: ErrorSpec(status.HTTP_404_NOT_FOUND, "not_found"),
    UserNotFoundError: ErrorSpec(status.HTTP_404_NOT_FOUND, "user_not_found"),
    RoomNotFoundError: ErrorSpec(status.HTTP_404_NOT_FOUND, "room_not_found"),
    BoxNotFoundError: ErrorSpec(status.HTTP_404_NOT_FOUND, "box_not_found"),
    ItemNotFoundError: ErrorSpec(status.HTTP_404_NOT_FOUND, "item_not_found"),
    RoomInUseError: ErrorSpec(status.HTTP_409_CONFLICT, "room_in_use"),
    BoxStatusConflictError: ErrorSpec(status.HTTP_409_CONFLICT, "box_status_conflict"),
    NotBoxCreatorError: ErrorSpec(status.HTTP_403_FORBIDDEN, "not_box_creator"),
    BoxNotPackingError: ErrorSpec(status.HTTP_409_CONFLICT, "box_not_packing"),
    ReminderInThePastError: ErrorSpec(
        status.HTTP_422_UNPROCESSABLE_CONTENT, "reminder_in_the_past"
    ),
    ItemBoxMismatchError: ErrorSpec(status.HTTP_422_UNPROCESSABLE_CONTENT, "item_box_mismatch"),
    UsernameAlreadyTakenError: ErrorSpec(status.HTTP_409_CONFLICT, "username_taken"),
    ProjectNotFoundError: ErrorSpec(status.HTTP_404_NOT_FOUND, "project_not_found"),
    UserOwnsProjectsError: ErrorSpec(status.HTTP_409_CONFLICT, "user_owns_projects"),
    RoomNameTakenError: ErrorSpec(status.HTTP_409_CONFLICT, "room_name_taken"),
    InvalidCredentialsError: ErrorSpec(status.HTTP_401_UNAUTHORIZED, "invalid_credentials"),
    InvalidRefreshTokenError: ErrorSpec(status.HTTP_401_UNAUTHORIZED, "invalid_refresh_token"),
    NotAuthenticatedError: ErrorSpec(status.HTTP_401_UNAUTHORIZED, "not_authenticated"),
    InvalidPaginationError: ErrorSpec(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_pagination"),
    RequestInProgressError: ErrorSpec(status.HTTP_409_CONFLICT, "request_in_progress"),
    IdempotencyKeyReusedError: ErrorSpec(
        status.HTTP_422_UNPROCESSABLE_CONTENT, "idempotency_key_reused"
    ),
}

# DomainErrors that are never the client's fault: a malformed hash comes from our
# storage, a finished notification is only ever touched by a buggy worker.
_INTERNAL_ERRORS = (InvalidPasswordHashError, NotificationAlreadyFinishedError)


class RateLimitedError(Exception):
    """Raised by the rate-limit dependency (an HTTP concern, not a use case outcome)."""

    def __init__(self, headers: Mapping[str, str]) -> None:
        super().__init__("Too many requests")
        self.headers = dict(headers)


type OpenApiResponses = dict[int | str, dict[str, Any]]


def error_doc(status_code: int, description: str) -> OpenApiResponses:
    """OpenAPI `responses=` entry documenting an error status of a route."""
    return {status_code: {"model": ErrorResponse, "description": description}}


# Documents the error shape on every versioned route.
ERROR_RESPONSES: OpenApiResponses = (
    error_doc(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid request")
    | error_doc(status.HTTP_429_TOO_MANY_REQUESTS, "Rate limited")
    | error_doc(status.HTTP_500_INTERNAL_SERVER_ERROR, "Internal error")
)


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    *,
    details: list[ErrorDetail] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            details=details or [],
            request_id=getattr(request.state, "request_id", None),
        )
    )
    return JSONResponse(
        status_code=status_code, content=body.model_dump(mode="json"), headers=headers
    )


def _spec_for(error: Exception) -> ErrorSpec | None:
    return next((ERROR_SPECS[cls] for cls in type(error).__mro__ if cls in ERROR_SPECS), None)


async def _handle_known_error(request: Request, error: Exception) -> JSONResponse:
    spec = _spec_for(error)
    if spec is None or isinstance(error, _INTERNAL_ERRORS):
        raise error  # becomes a 500 in _handle_unexpected_error
    headers = _UNAUTHORIZED_HEADERS if spec.status_code == status.HTTP_401_UNAUTHORIZED else None
    return error_response(
        request,
        spec.status_code,
        spec.code,
        str(error),
        headers=headers,
    )


async def _handle_rate_limited(request: Request, error: Exception) -> JSONResponse:
    headers = error.headers if isinstance(error, RateLimitedError) else None
    return error_response(
        request, status.HTTP_429_TOO_MANY_REQUESTS, "rate_limited", str(error), headers=headers
    )


async def _handle_request_validation(request: Request, error: Exception) -> JSONResponse:
    errors = error.errors() if isinstance(error, RequestValidationError) else []
    details = [
        ErrorDetail(field=".".join(str(part) for part in item["loc"]), message=item["msg"])
        for item in errors
    ]
    return error_response(
        request,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "validation_error",
        "Request validation failed",
        details=details,
    )


async def _handle_http_error(request: Request, error: Exception) -> JSONResponse:
    """Routing errors raised by Starlette itself: 404 for unknown paths, 405..."""
    if not isinstance(error, StarletteHTTPException):  # pragma: no cover
        raise error
    phrase = HTTPStatus(error.status_code).phrase
    code = phrase.lower().replace(" ", "_").replace("-", "_")
    message = error.detail or phrase
    return error_response(request, error.status_code, code, message, headers=error.headers)


async def _handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:  # noqa: ARG001
    # Runs in ServerErrorMiddleware, outside RequestContextMiddleware, so the
    # X-Request-ID header is set here. The traceback is logged by the middleware;
    # internals never reach the client.
    request_id = getattr(request.state, "request_id", None)
    return error_response(
        request,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal_error",
        "Internal server error",
        headers={REQUEST_ID_HEADER: request_id} if request_id else None,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _handle_known_error)
    app.add_exception_handler(ApplicationError, _handle_known_error)
    app.add_exception_handler(RateLimitedError, _handle_rate_limited)
    app.add_exception_handler(RequestValidationError, _handle_request_validation)
    app.add_exception_handler(StarletteHTTPException, _handle_http_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
