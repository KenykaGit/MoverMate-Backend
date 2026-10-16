from typing import Literal

from pydantic import BaseModel

from app.application.health.dto import Readiness


class LiveResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadyResponse(BaseModel):
    status: Readiness
    version: str
    checks: dict[str, Readiness]
