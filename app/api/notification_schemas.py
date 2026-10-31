"""Strict named Inbox and account-timezone contract 1.0."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID
from pydantic import Field, field_validator, model_validator
from app.api.planning_schemas import ClosedSchema
from app.domain.notifications import zone, InboxValidationError

Source = Literal["all", "today", "plan"]


class InboxTimezoneRequest(ClosedSchema):
    timezone: str = Field(min_length=1, max_length=128)

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        try:
            zone(value)
        except InboxValidationError:
            raise ValueError("Invalid IANA timezone") from None
        return value


class InboxTimezoneResponse(InboxTimezoneRequest):
    contract_version: Literal["1.0"]
    updated_at: datetime
    version: int = Field(ge=1)


class InboxSourceQuery(ClosedSchema):
    source: Source = "all"


class InboxEmptyQuery(ClosedSchema):
    pass


class InboxListQuery(InboxTimezoneRequest):
    source: Source = "all"
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(
        default=None,
        min_length=1,
        max_length=1024,
        description="Opaque account/filter/timezone-bound continuation; restart after expiry or mutation. Not a snapshot.",
    )


class InboxNavigationResponse(ClosedSchema):
    state: Literal["available", "unavailable"]
    action: Literal["today.view", "plan_item.view"] | None
    selected_date: date | None
    plan_item_id: UUID | None

    @model_validator(mode="after")
    def safe_destination(self):
        if self.state == "unavailable":
            if any(
                v is not None
                for v in (self.action, self.selected_date, self.plan_item_id)
            ):
                raise ValueError("Unavailable destination must omit references")
        elif (
            self.selected_date is None
            or self.action is None
            or ((self.action == "plan_item.view") != (self.plan_item_id is not None))
        ):
            raise ValueError("Invalid destination")
        return self


class InboxItemResponse(ClosedSchema):
    notification_id: UUID
    source: Literal["today", "plan"]
    category: Literal["reminder", "attention"]
    event_type: Literal["plan.item_starting_soon", "today.items_remaining"]
    title: str
    message: str
    created_at: datetime
    group: Literal["today", "earlier"]
    is_read: bool
    read_at: datetime | None
    source_timezone: str
    navigation: InboxNavigationResponse


class InboxListResponse(ClosedSchema):
    contract_version: Literal["1.0"]
    generated_at: datetime
    timezone: str
    filter: Source
    unread_count: int = Field(
        ge=0,
        description="All retained unread records matching the filter, not the loaded page",
    )
    items: list[InboxItemResponse]
    next_cursor: str | None
    has_more: bool


class InboxUnreadResponse(ClosedSchema):
    contract_version: Literal["1.0"]
    source: Source
    unread_count: int = Field(ge=0)
    generated_at: datetime


class InboxReadResponse(ClosedSchema):
    contract_version: Literal["1.0"]
    notification_id: UUID
    is_read: Literal[True]
    read_at: datetime
    source: Literal["all"]
    unread_count: int = Field(
        ge=0, description="Account-wide retained unread count across both sources"
    )


class InboxReadAllRequest(ClosedSchema):
    source: Source


class InboxReadAllResponse(ClosedSchema):
    contract_version: Literal["1.0"]
    source: Source
    marked_read_count: int = Field(ge=0)
    unread_count: int = Field(ge=0)
    read_at: datetime
