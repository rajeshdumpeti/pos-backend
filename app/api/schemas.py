"""HTTP request and response contracts for the local mock API."""

from typing import Any

from pydantic import BaseModel, Field, RootModel


class HealthResponse(BaseModel):
    status: str


class ResetResponse(BaseModel):
    status: str


class Item(BaseModel):
    id: str
    title: str
    meta: str = ""
    done: bool = False


class ItemPatch(RootModel[dict[str, Any]]):
    """Arbitrary partial fields preserved by the legacy local mock contract."""


class ErrorDetail(BaseModel):
    field: str
    code: str
    message: str


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str
    details: list[ErrorDetail] = Field(default_factory=list)
