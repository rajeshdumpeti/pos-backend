"""Named closed HTTP contracts for planning and Today v1."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID
import re
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)
from app.domain.planning import PlanIntent, PlanValidationError, timezone_info
from app.domain.today import (
    ActionIdentifier,
    CompletionState,
    DailyStatusCode,
    DomainType,
    GoalIdentifier,
    RecommendationCode,
    RecommendationState,
    ScoreState,
    SectionState,
    UpNextLabel,
)
from app.domain.onboarding import PriorityLifeArea


class ClosedSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


def non_null_schema(schema: dict) -> None:
    """Omitted fields use an internal sentinel; supplied null is not accepted."""
    if "anyOf" in schema:
        choices = [
            choice for choice in schema.pop("anyOf") if choice.get("type") != "null"
        ]
        if len(choices) == 1:
            schema.update(choices[0])
        else:
            schema["anyOf"] = choices
    schema.pop("default", None)


class PlanningInputSchema(ClosedSchema):
    @field_validator("scheduled_for_date", mode="before", check_fields=False)
    @classmethod
    def explicit_date(cls, value):
        if value is not None and not (
            type(value) is date
            or isinstance(value, str)
            and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value)
        ):
            raise ValueError("An ISO date is required")
        return value

    @field_validator(
        "scheduled_start_at", "scheduled_end_at", mode="before", check_fields=False
    )
    @classmethod
    def explicit_timestamp(cls, value):
        if value is not None and not (
            isinstance(value, datetime)
            or isinstance(value, str)
            and re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:[Zz]|[+-][0-9]{2}:[0-9]{2})",
                value,
            )
        ):
            raise ValueError("An explicit timestamp is required")
        return value


class CreatePlanItemRequest(PlanningInputSchema):
    title: str = Field(min_length=1, max_length=200, strict=True)
    scheduled_for_date: date
    timezone: str = Field(min_length=1, max_length=128, strict=True)
    scheduled_start_at: datetime | None = None
    scheduled_end_at: datetime | None = None
    is_priority: StrictBool = False

    @model_validator(mode="after")
    def validate_intent(self):
        try:
            PlanIntent(**self.model_dump()).normalized()
        except PlanValidationError:
            raise ValueError("Invalid planning intent") from None
        return self


class UpdatePlanItemRequest(PlanningInputSchema):
    expected_version: int = Field(ge=1, strict=True)
    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        strict=True,
        json_schema_extra=non_null_schema,
    )
    scheduled_for_date: date | None = Field(
        default=None, json_schema_extra=non_null_schema
    )
    timezone: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        strict=True,
        json_schema_extra=non_null_schema,
    )
    scheduled_start_at: datetime | None = None
    scheduled_end_at: datetime | None = None
    is_priority: StrictBool | None = Field(
        default=None, json_schema_extra=non_null_schema
    )

    @model_validator(mode="after")
    def validate_changes(self):
        supplied = self.model_fields_set - {"expected_version"}
        if not supplied:
            raise ValueError("An update field is required")
        for name in supplied - {"scheduled_start_at", "scheduled_end_at"}:
            if getattr(self, name) is None:
                raise ValueError("Field cannot be null")
        if self.title is not None and not self.title.strip():
            raise ValueError("Invalid title")
        if self.timezone is not None:
            try:
                timezone_info(self.timezone)
            except PlanValidationError:
                raise ValueError("Invalid timezone") from None
        for value in (self.scheduled_start_at, self.scheduled_end_at):
            if value is not None and value.utcoffset() is None:
                raise ValueError("An offset is required")
        return self


class CompletePlanItemRequest(ClosedSchema):
    expected_version: int = Field(ge=1, strict=True)


class PlanItemResponse(ClosedSchema):
    id: UUID
    title: str
    scheduled_for_date: date
    timezone: str
    scheduled_start_at: datetime | None
    scheduled_end_at: datetime | None
    is_priority: bool
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int


class TodayTimezoneQuery(ClosedSchema):
    timezone: str = Field(min_length=1, max_length=128)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            timezone_info(value)
        except PlanValidationError:
            raise ValueError("Invalid timezone") from None
        return value


class FreshnessResponse(ClosedSchema):
    state: Literal["current"]
    as_of: datetime


class DailyStatusResponse(ClosedSchema):
    state: SectionState
    code: DailyStatusCode
    eligible: int
    completed: int
    remaining: int
    overdue: int


class RecommendationEvidenceResponse(ClosedSchema):
    eligible: int
    remaining: int
    is_priority: bool | None
    scheduled_start_at: datetime | None


class RecommendationResponse(ClosedSchema):
    state: RecommendationState
    code: RecommendationCode
    domain: DomainType
    recommendation: str
    reason: str
    action: ActionIdentifier
    plan_item_id: UUID | None
    evidence: RecommendationEvidenceResponse


class ScoreContributionResponse(ClosedSchema):
    item_id: UUID
    domain: DomainType
    eligibility_reason: Literal["assigned_current_local_date"]
    weight: Literal[1]
    earned_weight: Literal[0, 1]
    completion_state: CompletionState


class ScoreResponse(ClosedSchema):
    state: ScoreState
    percentage: int | None
    numerator: int
    denominator: int
    breakdown: list[ScoreContributionResponse]


class ItemSummaryResponse(ClosedSchema):
    plan_item_id: UUID
    title: str
    scheduled_start_at: datetime | None
    scheduled_end_at: datetime | None
    scheduled_for_date: date
    timezone: str
    domain: DomainType
    state: CompletionState
    action: ActionIdentifier
    completed_at: datetime | None


class UpNextItemResponse(ClosedSchema):
    item: ItemSummaryResponse
    label: UpNextLabel


class UpNextSectionResponse(ClosedSchema):
    state: SectionState
    items: list[UpNextItemResponse] = Field(max_length=4)
    action: ActionIdentifier


class PrioritySectionResponse(ClosedSchema):
    state: SectionState
    items: list[ItemSummaryResponse] = Field(max_length=3)
    action: ActionIdentifier


class WinsSectionResponse(ClosedSchema):
    state: SectionState
    items: list[ItemSummaryResponse] = Field(max_length=3)
    action: ActionIdentifier


class ProgressResponse(ClosedSchema):
    state: SectionState
    reason: Literal["no_supported_measurement_sources"]
    metrics: list[Literal["reserved"]] = Field(max_length=0)


class CapabilitiesResponse(ClosedSchema):
    state: SectionState
    actions: list[ActionIdentifier]
    priority_life_areas: list[PriorityLifeArea]
    configured_goals: list[GoalIdentifier]
    measurement_sources: list[Literal["reserved"]] = Field(max_length=0)


class TodayResponse(ClosedSchema):
    contract_version: Literal["1.0"]
    generated_at: datetime
    timezone: str
    local_date: date
    freshness: FreshnessResponse
    daily_status: DailyStatusResponse
    next_best_action: RecommendationResponse
    score: ScoreResponse
    progress: ProgressResponse
    up_next: UpNextSectionResponse
    top_priorities: PrioritySectionResponse
    wins: WinsSectionResponse
    capabilities: CapabilitiesResponse
