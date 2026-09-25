"""Framework-independent onboarding contract and derived-state policy."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID


CONTRACT_VERSION = "1.0"


class PriorityLifeArea(StrEnum):
    HEALTH = "health"
    NUTRITION = "nutrition"
    FITNESS = "fitness"
    FOCUS = "focus"
    MONEY = "money"
    HABITS = "habits"
    SLEEP = "sleep"


class UnitSystem(StrEnum):
    METRIC = "metric"
    IMPERIAL = "imperial"


class Currency(StrEnum):
    USD = "USD"
    INR = "INR"
    CAD = "CAD"
    GBP = "GBP"
    EUR = "EUR"
    AUD = "AUD"


class IntegrationPreference(StrEnum):
    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    DEFERRED = "deferred"


class NotificationPreference(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    DEFERRED = "deferred"


PRIORITY_LIFE_AREAS = tuple(item.value for item in PriorityLifeArea)
UNIT_SYSTEMS = tuple(item.value for item in UnitSystem)
CURRENCIES = tuple(item.value for item in Currency)
INTEGRATION_IDENTIFIERS = (
    "apple_health",
    "health_connect",
    "apple_watch",
    "calendar",
    "reminders",
    "wearables",
)
NOTIFICATION_IDENTIFIERS = (
    "workout",
    "protein",
    "water",
    "finance",
    "deep_work",
    "weekly_reflection",
)
GOAL_FIELDS = (
    "target_weight",
    "daily_protein_grams",
    "daily_water",
    "daily_calories_kcal",
    "daily_steps",
    "workouts_per_week",
    "focus_minutes_per_day",
    "target_sleep_minutes",
    "budget_target",
)


@dataclass(frozen=True, slots=True)
class OnboardingState:
    user_account_id: UUID
    version: int
    priority_life_areas: tuple[str, ...] = ()
    unit_system: str | None = None
    unit_system_confirmed: bool = False
    currency: str | None = None
    currency_confirmed: bool = False
    goals: dict[str, Any] = field(default_factory=dict)
    integration_preferences: dict[str, str] = field(default_factory=dict)
    notification_preferences: dict[str, str] = field(default_factory=dict)
    completed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    contract_version: str = CONTRACT_VERSION

    @property
    def ready_to_complete(self) -> bool:
        return bool(
            self.priority_life_areas
            and self.unit_system in UNIT_SYSTEMS
            and self.unit_system_confirmed
            and self.currency in CURRENCIES
            and self.currency_confirmed
        )

    @property
    def is_complete(self) -> bool:
        """Return canonical completion after all required facts are satisfied."""
        return self.completed_at is not None and self.ready_to_complete

    @property
    def status(self) -> str:
        if self.is_complete:
            return "completed"
        if self.meaningful_data_exists:
            return "in_progress"
        return "not_started"

    @property
    def meaningful_data_exists(self) -> bool:
        return bool(
            self.priority_life_areas
            or self.unit_system_confirmed
            or self.currency_confirmed
            or self.goals
            or self.integration_preferences
            or self.notification_preferences
        )

    @property
    def recommended_next_action(self) -> str:
        if self.is_complete:
            return "onboarding_completed"
        if not self.priority_life_areas:
            return "select_priority_life_areas"
        if not (self.unit_system in UNIT_SYSTEMS and self.unit_system_confirmed):
            return "confirm_unit_system"
        if not (self.currency in CURRENCIES and self.currency_confirmed):
            return "confirm_currency"
        return "complete_onboarding"


def normalize_priorities(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Deduplicate priorities in canonical product order."""
    selected = set(values)
    unknown = selected.difference(PRIORITY_LIFE_AREAS)
    if unknown:
        raise ValueError("Unsupported priority life area")
    return tuple(value for value in PRIORITY_LIFE_AREAS if value in selected)


def decimal_to_storage(value: Decimal) -> str:
    """Return deterministic exact JSON storage without binary floating point."""
    return format(value, "f")


class CorruptOnboardingStateError(ValueError):
    """Persisted onboarding state failed the frozen contract."""


def validate_persisted_state(state: OnboardingState) -> OnboardingState:
    """Strictly reconstruct a row without exposing corrupted stored values."""
    try:
        if (
            state.contract_version != CONTRACT_VERSION
            or type(state.version) is not int
            or state.version < 1
            or type(state.unit_system_confirmed) is not bool
            or type(state.currency_confirmed) is not bool
        ):
            raise CorruptOnboardingStateError("Persisted onboarding state is invalid")
        if not isinstance(state.priority_life_areas, tuple) or any(
            not isinstance(value, str) for value in state.priority_life_areas
        ):
            raise CorruptOnboardingStateError("Persisted onboarding state is invalid")
        try:
            priorities = normalize_priorities(state.priority_life_areas)
        except ValueError as error:
            raise CorruptOnboardingStateError(
                "Persisted onboarding state is invalid"
            ) from error
        if priorities != state.priority_life_areas:
            raise CorruptOnboardingStateError("Persisted onboarding state is invalid")
        if state.unit_system not in (*UNIT_SYSTEMS, None):
            raise CorruptOnboardingStateError("Persisted onboarding state is invalid")
        if state.currency not in (*CURRENCIES, None):
            raise CorruptOnboardingStateError("Persisted onboarding state is invalid")
        if state.unit_system_confirmed and state.unit_system is None:
            raise CorruptOnboardingStateError("Persisted onboarding state is invalid")
        if state.currency_confirmed and state.currency is None:
            raise CorruptOnboardingStateError("Persisted onboarding state is invalid")
        if not _aware_timestamp(state.created_at) or not _aware_timestamp(
            state.updated_at
        ):
            raise CorruptOnboardingStateError("Persisted onboarding state is invalid")
        if state.created_at > state.updated_at:
            raise CorruptOnboardingStateError("Persisted onboarding state is invalid")
        if state.completed_at is not None:
            if not _aware_timestamp(state.completed_at):
                raise CorruptOnboardingStateError(
                    "Persisted onboarding state is invalid"
                )
            if not state.created_at <= state.completed_at <= state.updated_at:
                raise CorruptOnboardingStateError(
                    "Persisted onboarding state is invalid"
                )
            if not state.ready_to_complete:
                raise CorruptOnboardingStateError(
                    "Persisted onboarding state is invalid"
                )
        goals = _validate_goals(state.goals)
        integrations = _validate_preferences(
            state.integration_preferences,
            frozenset(INTEGRATION_IDENTIFIERS),
            frozenset(item.value for item in IntegrationPreference),
        )
        notifications = _validate_preferences(
            state.notification_preferences,
            frozenset(NOTIFICATION_IDENTIFIERS),
            frozenset(item.value for item in NotificationPreference),
        )
        budget = goals.get("budget_target")
        if budget is not None and (
            not state.currency_confirmed or budget["currency"] != state.currency
        ):
            raise CorruptOnboardingStateError("Persisted onboarding state is invalid")
        return OnboardingState(
            user_account_id=state.user_account_id,
            version=state.version,
            priority_life_areas=priorities,
            unit_system=state.unit_system,
            unit_system_confirmed=state.unit_system_confirmed,
            currency=state.currency,
            currency_confirmed=state.currency_confirmed,
            goals=goals,
            integration_preferences=integrations,
            notification_preferences=notifications,
            completed_at=state.completed_at,
            created_at=state.created_at,
            updated_at=state.updated_at,
            contract_version=state.contract_version,
        )
    except (TypeError, AttributeError, KeyError, ArithmeticError, ValueError) as error:
        raise CorruptOnboardingStateError(
            "Persisted onboarding state is invalid"
        ) from error


def _aware_timestamp(value: Any) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _decimal_string(value: Any, *, positive: bool, places: int) -> str:
    if not isinstance(value, str) or len(value) > 32:
        raise CorruptOnboardingStateError("Persisted onboarding state is invalid")
    try:
        decimal = Decimal(value)
    except Exception as error:
        raise CorruptOnboardingStateError(
            "Persisted onboarding state is invalid"
        ) from error
    if not decimal.is_finite() or (decimal <= 0 if positive else decimal < 0):
        raise CorruptOnboardingStateError("Persisted onboarding state is invalid")
    exponent = decimal.as_tuple().exponent
    if (
        len(decimal.as_tuple().digits) > 18
        or decimal.adjusted() >= 18
        or exponent < -places
    ):
        raise CorruptOnboardingStateError("Persisted onboarding state is invalid")
    return format(decimal, "f")


def _integer(value: Any, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise CorruptOnboardingStateError("Persisted onboarding state is invalid")
    return value


def _validate_goals(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value).difference(GOAL_FIELDS):
        raise CorruptOnboardingStateError("Persisted onboarding state is invalid")
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key == "target_weight":
            if (
                not isinstance(item, dict)
                or set(item) != {"value", "unit"}
                or item["unit"] not in {"kg", "lb"}
            ):
                raise CorruptOnboardingStateError(
                    "Persisted onboarding state is invalid"
                )
            result[key] = {
                "value": _decimal_string(item["value"], positive=True, places=6),
                "unit": item["unit"],
            }
        elif key == "daily_water":
            if (
                not isinstance(item, dict)
                or set(item) != {"value", "unit"}
                or item["unit"] not in {"liters", "fluid_ounces"}
            ):
                raise CorruptOnboardingStateError(
                    "Persisted onboarding state is invalid"
                )
            result[key] = {
                "value": _decimal_string(item["value"], positive=True, places=6),
                "unit": item["unit"],
            }
        elif key == "budget_target":
            if (
                not isinstance(item, dict)
                or set(item) != {"amount", "currency"}
                or item["currency"] not in CURRENCIES
            ):
                raise CorruptOnboardingStateError(
                    "Persisted onboarding state is invalid"
                )
            result[key] = {
                "amount": _decimal_string(item["amount"], positive=False, places=2),
                "currency": item["currency"],
            }
        elif key == "daily_protein_grams":
            result[key] = _decimal_string(item, positive=True, places=6)
        elif key == "daily_calories_kcal":
            result[key] = _integer(item, 1, 10_000_000)
        elif key == "daily_steps":
            result[key] = _integer(item, 1, 100_000_000)
        elif key == "workouts_per_week":
            result[key] = _integer(item, 0, 7)
        elif key == "focus_minutes_per_day":
            result[key] = _integer(item, 0, 1440)
        elif key == "target_sleep_minutes":
            result[key] = _integer(item, 1, 1440)
    return result


def _validate_preferences(
    value: Any, identifiers: frozenset[str], values: frozenset[str]
) -> dict[str, str]:
    if not isinstance(value, dict) or set(value).difference(identifiers):
        raise CorruptOnboardingStateError("Persisted onboarding state is invalid")
    if any(not isinstance(item, str) or item not in values for item in value.values()):
        raise CorruptOnboardingStateError("Persisted onboarding state is invalid")
    return dict(sorted(value.items()))
