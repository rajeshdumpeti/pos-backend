"""Onboarding use cases, transaction ports, and safe application errors."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Protocol, Self
from uuid import UUID

from app.application.account_eligibility import AccountUnavailableError
from app.domain.authentication import AccountStatus
from app.domain.onboarding import (
    CorruptOnboardingStateError,
    OnboardingState,
    normalize_priorities,
    validate_persisted_state,
)


class OnboardingError(RuntimeError):
    pass


class OnboardingUpdateRejectedError(OnboardingError):
    pass


class OnboardingIncompleteError(OnboardingError):
    pass


class OnboardingStateConflictError(OnboardingError):
    pass


class OnboardingRequiredError(OnboardingError):
    pass


class OnboardingEmailVerificationRequiredError(OnboardingError):
    pass


class OnboardingDependencyUnavailableError(OnboardingError):
    pass


class OnboardingRepository(Protocol):
    def get(self, account_id: UUID) -> OnboardingState | None: ...
    def get_for_update(self, account_id: UUID) -> OnboardingState | None: ...
    def add(self, state: OnboardingState) -> None: ...
    def update(self, state: OnboardingState, expected_version: int) -> bool: ...


class OnboardingAccountRepository(Protocol):
    def get(self, account_id: UUID) -> Any | None: ...
    def set_onboarding_required(
        self, account_id: UUID, required: bool, updated_at: datetime
    ) -> bool: ...


class OnboardingUnitOfWork(Protocol):
    @property
    def onboarding(self) -> OnboardingRepository: ...
    @property
    def accounts(self) -> OnboardingAccountRepository: ...
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class OnboardingDiagnostic(Protocol):
    def emit(
        self,
        *,
        event: str,
        operation: str,
        outcome: str,
        request_id: str,
        account_id: UUID,
        version: int,
        priority_count: int,
        category: str | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class OnboardingUpdate:
    expected_version: int
    changes: dict[str, Any]


class OnboardingService:
    def __init__(
        self,
        unit_of_work_factory: Callable[[str], OnboardingUnitOfWork],
        *,
        clock: Callable[[], datetime] | None = None,
        diagnostic: OnboardingDiagnostic | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._diagnostic = diagnostic

    def get(self, account_id: UUID, request_id: str) -> OnboardingState:
        try:
            with self._unit_of_work_factory(request_id) as unit:
                self._require_eligible(unit, account_id)
                state = unit.onboarding.get(account_id) or OnboardingState(
                    account_id, 0
                )
                self._emit(
                    "onboarding_state_retrieved", "get", "succeeded", request_id, state
                )
                return state
        except OnboardingDependencyUnavailableError:
            raise

    def update(
        self, account_id: UUID, command: OnboardingUpdate, request_id: str
    ) -> OnboardingState:
        if not command.changes:
            raise OnboardingUpdateRejectedError("At least one update field is required")
        now = self._clock()
        try:
            with self._unit_of_work_factory(request_id) as unit:
                self._require_eligible(unit, account_id)
                current = unit.onboarding.get_for_update(account_id)
                if current is None:
                    current = OnboardingState(account_id, 0)
                try:
                    intended = self._apply(current, command.changes, now)
                except (AttributeError, TypeError, ValueError) as error:
                    raise OnboardingUpdateRejectedError(
                        "Onboarding update was rejected"
                    ) from error
                if self._same_persisted_state(current, intended):
                    return current
                if command.expected_version != current.version:
                    raise OnboardingStateConflictError(
                        "Onboarding state version conflicts"
                    )
                intended = replace(intended, version=current.version + 1)
                try:
                    intended = validate_persisted_state(intended)
                except CorruptOnboardingStateError as error:
                    raise OnboardingUpdateRejectedError(
                        "Onboarding update was rejected"
                    ) from error
                if current.version == 0:
                    unit.onboarding.add(intended)
                elif not unit.onboarding.update(intended, current.version):
                    raise OnboardingStateConflictError(
                        "Onboarding state version conflicts"
                    )
                unit.commit()
                self._emit(
                    (
                        "onboarding_state_created"
                        if current.version == 0
                        else "onboarding_state_updated"
                    ),
                    "update",
                    "succeeded",
                    request_id,
                    intended,
                )
                return intended
        except OnboardingStateConflictError:
            self._emit(
                "onboarding_state_conflict",
                "update",
                "rejected",
                request_id,
                OnboardingState(account_id, command.expected_version),
                category="concurrency",
            )
            raise

    def complete(
        self, account_id: UUID, expected_version: int, request_id: str
    ) -> OnboardingState:
        now = self._clock()
        with self._unit_of_work_factory(request_id) as unit:
            self._require_eligible(unit, account_id)
            current = unit.onboarding.get_for_update(account_id)
            if current is None:
                self._emit(
                    "onboarding_completion_rejected",
                    "complete",
                    "rejected",
                    request_id,
                    OnboardingState(account_id, 0),
                    category="incomplete",
                )
                raise OnboardingIncompleteError(
                    "Onboarding requirements are incomplete"
                )
            if current.is_complete:
                account = unit.accounts.get(account_id)
                if account is not None and account.onboarding_required:
                    if not unit.accounts.set_onboarding_required(
                        account_id, False, now
                    ):
                        raise OnboardingDependencyUnavailableError(
                            "Account state could not be updated"
                        )
                    unit.commit()
                return current
            if expected_version != current.version:
                self._emit(
                    "onboarding_completion_rejected",
                    "complete",
                    "rejected",
                    request_id,
                    current,
                    category="concurrency",
                )
                raise OnboardingStateConflictError("Onboarding state version conflicts")
            if not current.ready_to_complete:
                self._emit(
                    "onboarding_completion_rejected",
                    "complete",
                    "rejected",
                    request_id,
                    current,
                    category="incomplete",
                )
                raise OnboardingIncompleteError(
                    "Onboarding requirements are incomplete"
                )
            completed = replace(
                current, version=current.version + 1, completed_at=now, updated_at=now
            )
            if not unit.onboarding.update(completed, current.version):
                raise OnboardingStateConflictError("Onboarding state version conflicts")
            if not unit.accounts.set_onboarding_required(account_id, False, now):
                raise OnboardingDependencyUnavailableError(
                    "Account state could not be updated"
                )
            unit.commit()
            self._emit(
                "onboarding_completed", "complete", "succeeded", request_id, completed
            )
            return completed

    def require_completed(self, account_id: UUID, request_id: str) -> OnboardingState:
        state = self.get(account_id, request_id)
        if not state.is_complete:
            self._emit(
                "onboarding_required",
                "gate",
                "rejected",
                request_id,
                state,
                category="incomplete",
            )
            raise OnboardingRequiredError("Onboarding completion is required")
        return state

    def _emit(
        self,
        event: str,
        operation: str,
        outcome: str,
        request_id: str,
        state: OnboardingState,
        category: str | None = None,
    ) -> None:
        if self._diagnostic is None:
            return
        self._diagnostic.emit(
            event=event,
            operation=operation,
            outcome=outcome,
            request_id=request_id,
            account_id=state.user_account_id,
            version=state.version,
            priority_count=len(state.priority_life_areas),
            category=category,
        )

    @staticmethod
    def _require_eligible(unit: OnboardingUnitOfWork, account_id: UUID) -> None:
        account = unit.accounts.get(account_id)
        if account is None or account.status is not AccountStatus.ACTIVE:
            raise AccountUnavailableError("Account is unavailable")
        if account.email_verified_at is None:
            raise OnboardingEmailVerificationRequiredError(
                "Email verification is required"
            )

    @staticmethod
    def _apply(
        current: OnboardingState, changes: dict[str, Any], now: datetime
    ) -> OnboardingState:
        values = dict(changes)
        for field_name in (
            "goals",
            "integration_preferences",
            "notification_preferences",
        ):
            if field_name in values:
                values[field_name] = {
                    **getattr(current, field_name),
                    **values[field_name],
                }
        if "priority_life_areas" in values:
            values["priority_life_areas"] = normalize_priorities(
                values["priority_life_areas"]
            )
        unit_value = values.get("unit_system", current.unit_system)
        unit_confirmed = values.get(
            "unit_system_confirmed", current.unit_system_confirmed
        )
        currency = values.get("currency", current.currency)
        currency_confirmed = values.get(
            "currency_confirmed", current.currency_confirmed
        )
        if unit_confirmed and unit_value is None:
            raise OnboardingUpdateRejectedError(
                "Unit confirmation requires a selected value"
            )
        if currency_confirmed and currency is None:
            raise OnboardingUpdateRejectedError(
                "Currency confirmation requires a selected value"
            )
        if (
            "unit_system" in values
            and values["unit_system"] != current.unit_system
            and "unit_system_confirmed" not in values
        ):
            values["unit_system_confirmed"] = False
        if (
            "currency" in values
            and values["currency"] != current.currency
            and "currency_confirmed" not in values
        ):
            values["currency_confirmed"] = False
        goals = values.get("goals", current.goals)
        budget = goals.get("budget_target") if goals else None
        if budget is not None and (
            not currency_confirmed or budget.get("currency") != currency
        ):
            raise OnboardingUpdateRejectedError(
                "Budget currency must match the confirmed onboarding currency"
            )
        return replace(
            current, **values, updated_at=now, created_at=current.created_at or now
        )

    @staticmethod
    def _same_persisted_state(left: OnboardingState, right: OnboardingState) -> bool:
        ignored = {"version", "created_at", "updated_at"}
        return all(
            getattr(left, name) == getattr(right, name)
            for name in left.__dataclass_fields__
            if name not in ignored
        )
