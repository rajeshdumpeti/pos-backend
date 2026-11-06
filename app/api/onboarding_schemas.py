"""Strict public schemas for the authoritative onboarding contract."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.onboarding import (
    CURRENCIES,
    GOAL_FIELDS,
    INTEGRATION_IDENTIFIERS,
    NOTIFICATION_IDENTIFIERS,
    PRIORITY_LIFE_AREAS,
    UNIT_SYSTEMS,
)


PositiveDecimal = Annotated[
    Decimal, Field(gt=0, max_digits=18, decimal_places=6, allow_inf_nan=False)
]
MoneyDecimal = Annotated[
    Decimal, Field(ge=0, max_digits=18, decimal_places=2, allow_inf_nan=False)
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class StrictResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MeasuredValue(StrictModel):
    value: PositiveDecimal
    unit: Literal["kg", "lb"]


class WaterValue(StrictModel):
    value: PositiveDecimal
    unit: Literal["liters", "fluid_ounces"]


class BudgetValue(StrictModel):
    amount: MoneyDecimal
    currency: Literal["USD", "INR", "CAD", "GBP", "EUR", "AUD"]


class OnboardingGoals(StrictModel):
    target_weight: MeasuredValue | None = None
    daily_protein_grams: PositiveDecimal | None = None
    daily_water: WaterValue | None = None
    daily_calories_kcal: int | None = Field(default=None, gt=0, le=10_000_000)
    daily_steps: int | None = Field(default=None, gt=0, le=100_000_000)
    workouts_per_week: int | None = Field(default=None, ge=0, le=7)
    focus_minutes_per_day: int | None = Field(default=None, ge=0, le=1440)
    target_sleep_minutes: int | None = Field(default=None, gt=0, le=1440)
    budget_target: BudgetValue | None = None


class OnboardingPatchRequest(StrictModel):
    expected_version: int = Field(ge=0)
    priority_life_areas: (
        list[
            Literal[
                "health", "nutrition", "fitness", "focus", "money", "habits", "sleep"
            ]
        ]
        | None
    ) = Field(default=None, max_length=7)
    unit_system: Literal["metric", "imperial"] | None = None
    unit_system_confirmed: bool | None = None
    currency: Literal["USD", "INR", "CAD", "GBP", "EUR", "AUD"] | None = None
    currency_confirmed: bool | None = None
    goals: OnboardingGoals | None = None
    integration_preferences: (
        dict[
            Literal[
                "apple_health",
                "health_connect",
                "apple_watch",
                "calendar",
                "reminders",
                "wearables",
            ],
            Literal["interested", "not_interested", "deferred"],
        ]
        | None
    ) = None
    notification_preferences: (
        dict[
            Literal[
                "workout",
                "protein",
                "water",
                "finance",
                "deep_work",
                "weekly_reflection",
            ],
            Literal["enabled", "disabled", "deferred"],
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def require_change(self) -> "OnboardingPatchRequest":
        if self.model_fields_set == {"expected_version"}:
            raise ValueError("At least one update field is required")
        return self


class CompleteOnboardingRequest(StrictModel):
    expected_version: int = Field(ge=0)


class UnitPreferenceResponse(StrictResponseModel):
    value: Literal["metric", "imperial"] | None
    confirmed: bool


class CurrencyPreferenceResponse(StrictResponseModel):
    value: Literal["USD", "INR", "CAD", "GBP", "EUR", "AUD"] | None
    confirmed: bool


class MeasuredValueResponse(StrictResponseModel):
    value: str
    unit: Literal["kg", "lb"]


class WaterValueResponse(StrictResponseModel):
    value: str
    unit: Literal["liters", "fluid_ounces"]


class BudgetValueResponse(StrictResponseModel):
    amount: str
    currency: Literal["USD", "INR", "CAD", "GBP", "EUR", "AUD"]


class OnboardingGoalsResponse(StrictResponseModel):
    target_weight: MeasuredValueResponse | None = None
    daily_protein_grams: str | None = None
    daily_water: WaterValueResponse | None = None
    daily_calories_kcal: int | None = None
    daily_steps: int | None = None
    workouts_per_week: int | None = None
    focus_minutes_per_day: int | None = None
    target_sleep_minutes: int | None = None
    budget_target: BudgetValueResponse | None = None


class IntegrationPreferencesResponse(StrictResponseModel):
    apple_health: Literal["interested", "not_interested", "deferred"] | None = None
    health_connect: Literal["interested", "not_interested", "deferred"] | None = None
    apple_watch: Literal["interested", "not_interested", "deferred"] | None = None
    calendar: Literal["interested", "not_interested", "deferred"] | None = None
    reminders: Literal["interested", "not_interested", "deferred"] | None = None
    wearables: Literal["interested", "not_interested", "deferred"] | None = None


class NotificationPreferencesResponse(StrictResponseModel):
    workout: Literal["enabled", "disabled", "deferred"] | None = None
    protein: Literal["enabled", "disabled", "deferred"] | None = None
    water: Literal["enabled", "disabled", "deferred"] | None = None
    finance: Literal["enabled", "disabled", "deferred"] | None = None
    deep_work: Literal["enabled", "disabled", "deferred"] | None = None
    weekly_reflection: Literal["enabled", "disabled", "deferred"] | None = None


class RequirementsResponse(StrictResponseModel):
    priority_life_areas_satisfied: bool
    unit_system_satisfied: bool
    currency_satisfied: bool
    ready_to_complete: bool


class OnboardingConfigurationResponse(StrictResponseModel):
    priority_life_areas: list[
        Literal["health", "nutrition", "fitness", "focus", "money", "habits", "sleep"]
    ]
    unit_systems: list[Literal["metric", "imperial"]]
    currencies: list[Literal["USD", "INR", "CAD", "GBP", "EUR", "AUD"]]
    optional_goal_fields: list[
        Literal[
            "target_weight",
            "daily_protein_grams",
            "daily_water",
            "daily_calories_kcal",
            "daily_steps",
            "workouts_per_week",
            "focus_minutes_per_day",
            "target_sleep_minutes",
            "budget_target",
        ]
    ]
    integration_identifiers: list[
        Literal[
            "apple_health",
            "health_connect",
            "apple_watch",
            "calendar",
            "reminders",
            "wearables",
        ]
    ]
    integration_preference_values: list[
        Literal["interested", "not_interested", "deferred"]
    ]
    notification_identifiers: list[
        Literal[
            "workout", "protein", "water", "finance", "deep_work", "weekly_reflection"
        ]
    ]
    notification_preference_values: list[Literal["enabled", "disabled", "deferred"]]
    routine_creation_supported: bool


class OnboardingResponse(StrictResponseModel):
    status: Literal["not_started", "in_progress", "completed"]
    contract_version: Literal["1.0"]
    version: int
    priority_life_areas: list[
        Literal["health", "nutrition", "fitness", "focus", "money", "habits", "sleep"]
    ]
    unit_preference: UnitPreferenceResponse
    currency_preference: CurrencyPreferenceResponse
    goals: OnboardingGoalsResponse
    integration_preferences: IntegrationPreferencesResponse
    notification_preferences: NotificationPreferencesResponse
    requirements: RequirementsResponse
    recommended_next_action: Literal[
        "select_priority_life_areas",
        "confirm_unit_system",
        "confirm_currency",
        "complete_onboarding",
        "onboarding_completed",
    ]
    completed_at: datetime | None
    configuration: OnboardingConfigurationResponse


CONFIGURATION = OnboardingConfigurationResponse(
    priority_life_areas=list(PRIORITY_LIFE_AREAS),
    unit_systems=list(UNIT_SYSTEMS),
    currencies=list(CURRENCIES),
    optional_goal_fields=list(GOAL_FIELDS),
    integration_identifiers=list(INTEGRATION_IDENTIFIERS),
    integration_preference_values=["interested", "not_interested", "deferred"],
    notification_identifiers=list(NOTIFICATION_IDENTIFIERS),
    notification_preference_values=["enabled", "disabled", "deferred"],
    routine_creation_supported=False,
)
