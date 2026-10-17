from typing import Self

from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field

from app.application.common.pagination import Page
from app.application.common.patch import UNSET, Patch


class RequestSchema(BaseModel):
    """Base of request bodies: only the shape is checked here.

    Business rules (username format, title length, password policy...) live in
    the domain; violations are reported as 422 in the same error format.
    """

    model_config = ConfigDict(extra="forbid")


class PatchRequest(RequestSchema):
    """Base of PATCH bodies: a field that is absent stays as it is.

    Optional fields declare `X | None = None` so that absence can be told apart
    from an explicit null (`model_fields_set`). A field that cannot be cleared
    rejects null with the usual 422 `validation_error`.
    """

    def sent[T](self, name: str, value: T) -> Patch[T]:
        """`value` if the client sent the field (null included), else UNSET."""
        return value if name in self.model_fields_set else UNSET

    def sent_not_null[T](self, name: str, value: T | None) -> Patch[T]:
        """Like `sent`, for a field that may be omitted but not set to null."""
        if name not in self.model_fields_set:
            return UNSET
        if value is None:
            raise RequestValidationError(
                [{"loc": ("body", name), "msg": "May be omitted, but not null", "type": "null"}]
            )
        return value


class ErrorDetail(BaseModel):
    field: str | None = Field(default=None, examples=["body.title"])
    message: str


class ErrorBody(BaseModel):
    code: str = Field(examples=["username_taken"], description="Stable, machine-readable")
    message: str = Field(description="For humans; may change")
    details: list[ErrorDetail] = Field(default_factory=list)
    request_id: str | None = Field(default=None, description="Same as the X-Request-ID header")


class ErrorResponse(BaseModel):
    """Every non-2xx response of the API has this shape (nginx's own errors too)."""

    error: ErrorBody


class PageResponse[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int

    @classmethod
    def of[DTO](cls, page: Page[DTO], items: list[T]) -> Self:
        return cls(items=items, total=page.total, limit=page.limit, offset=page.offset)
