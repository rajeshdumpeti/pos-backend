"""Named, closed selected-date Plan contract 1.0."""

import re
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.api.planning_schemas import ClosedSchema, PlanItemResponse, TodayTimezoneQuery
from app.domain.plan_read import (
    PlanDateRelation,
    PlanInsightAction,
    PlanInsightCode,
    PlanInsightState,
    PlanItemSection,
    PlanTimingState,
)


class PlanSelectedDateQuery(TodayTimezoneQuery):
    selected_date: date = Field(
        description="Explicit assigned date, YYYY-MM-DD; independent of start conversion"
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=100,
        description="Maximum returned items; summary and insight cover the full date",
    )
    cursor: str | None = Field(
        default=None,
        min_length=1,
        max_length=1024,
        description="Opaque continuation from the preceding response; bound to account, date and timezone",
    )

    @field_validator("selected_date", mode="before")
    @classmethod
    def explicit_date(cls, value):
        if not (
            type(value) is date
            or isinstance(value, str)
            and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value)
        ):
            raise ValueError("An ISO date is required")
        return value


class PlanFreshnessResponse(ClosedSchema):
    state: Literal["current"]
    as_of: datetime


class PlanSummaryResponse(ClosedSchema):
    total: int = Field(ge=0)
    completed: int = Field(ge=0)
    remaining: int = Field(ge=0)
    scheduled_remaining: int = Field(ge=0)
    unscheduled_remaining: int = Field(ge=0)
    completion_percentage: int | None = Field(
        ge=0,
        le=100,
        description="Equal-weight full-date completion, Decimal ROUND_HALF_UP; null for no items",
    )


class PlanDayItemResponse(PlanItemResponse):
    section: PlanItemSection
    timing_state: PlanTimingState
    action: Literal["plan_item.view"]


class PlanPageResponse(ClosedSchema):
    limit: int = Field(ge=1, le=100)
    next_cursor: str | None
    has_more: bool


class PlanInsightEvidenceResponse(ClosedSchema):
    selected_date: date
    date_relation: PlanDateRelation
    total: int
    completed: int
    remaining: int
    is_priority: bool | None
    timing_state: PlanTimingState | None
    scheduled_start_at: datetime | None


class PlanInsightResponse(ClosedSchema):
    state: PlanInsightState
    code: PlanInsightCode
    recommendation: str
    reason: str
    action: PlanInsightAction | None
    plan_item_id: UUID | None
    evidence: PlanInsightEvidenceResponse


class PlanSelectedDateResponse(ClosedSchema):
    contract_version: Literal["1.0"]
    generated_at: datetime
    timezone: str
    selected_date: date
    date_relation: PlanDateRelation
    freshness: PlanFreshnessResponse
    summary: PlanSummaryResponse
    insight: PlanInsightResponse
    items: list[PlanDayItemResponse] = Field(max_length=100)
    page: PlanPageResponse
