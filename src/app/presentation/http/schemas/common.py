from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from app.application.common.pagination import Page


class RequestSchema(BaseModel):
    """Base of request bodies: only the shape is checked here.

    Business rules (username format, title length, password policy...) live in
    the domain; violations are reported as 422 in the same error format.
    """

    model_config = ConfigDict(extra="forbid")


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
