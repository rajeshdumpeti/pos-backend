from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.onboarding import (
    OnboardingIncompleteError,
    OnboardingRequiredError,
    OnboardingService,
    OnboardingStateConflictError,
    OnboardingUpdate,
    OnboardingUpdateRejectedError,
)
from app.application.account_eligibility import AccountUnavailableError
from app.application.onboarding import OnboardingEmailVerificationRequiredError
from app.application.onboarding import OnboardingDependencyUnavailableError
from app.domain.authentication import AccountStatus
from app.domain.onboarding import (
    CorruptOnboardingStateError,
    OnboardingState,
    normalize_priorities,
    validate_persisted_state,
)


NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)


class Repository:
    def __init__(self, states):
        self.states = states

    def get(self, account_id):
        return self.states.get(account_id)

    def get_for_update(self, account_id):
        return self.states.get(account_id)

    def add(self, state):
        if state.user_account_id in self.states:
            raise OnboardingStateConflictError()
        self.states[state.user_account_id] = state

    def update(self, state, expected_version):
        current = self.states.get(state.user_account_id)
        if current is None or current.version != expected_version:
            return False
        self.states[state.user_account_id] = state
        return True


class Accounts:
    def __init__(self, account):
        self.account = account

    def get(self, account_id):
        return self.account if self.account.id == account_id else None

    def set_onboarding_required(self, account_id, required, updated_at):
        if self.account.id != account_id:
            return False
        self.account.onboarding_required = required
        return True


class Unit:
    def __init__(self, states, account):
        self.onboarding, self.accounts = Repository(states), Accounts(account)
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


def fixture_service():
    account_id = uuid4()
    account = SimpleNamespace(
        id=account_id,
        status=AccountStatus.ACTIVE,
        email_verified_at=NOW,
        onboarding_required=True,
    )
    states = {}
    units = []

    def factory(_request_id):
        unit = Unit(states, account)
        units.append(unit)
        return unit

    return (
        account_id,
        account,
        states,
        units,
        OnboardingService(factory, clock=lambda: NOW),
    )


def test_virtual_state_and_deterministic_priority_normalization():
    account_id, _, _, _, service = fixture_service()
    state = service.get(account_id, "request")
    assert state.status == "not_started"
    assert state.version == 0
    assert state.recommended_next_action == "select_priority_life_areas"
    assert normalize_priorities(["focus", "health", "focus"]) == ("health", "focus")


def test_update_readiness_completion_and_idempotency():
    account_id, account, states, _, service = fixture_service()
    state = service.update(
        account_id,
        OnboardingUpdate(
            0,
            {
                "priority_life_areas": ["focus", "health", "focus"],
                "unit_system": "metric",
                "unit_system_confirmed": True,
                "currency": "USD",
                "currency_confirmed": True,
            },
        ),
        "request",
    )
    assert state.version == 1 and state.ready_to_complete
    assert state.priority_life_areas == ("health", "focus")
    completed = service.complete(account_id, 1, "request")
    assert completed.status == "completed" and completed.version == 2
    assert completed.completed_at == NOW and account.onboarding_required is False
    account.onboarding_required = True
    assert service.complete(account_id, 0, "request") == completed
    assert account.onboarding_required is False
    assert states[account_id] == completed


def test_stale_conflict_identical_replay_and_incomplete_completion():
    account_id, _, _, units, service = fixture_service()
    state = service.update(
        account_id, OnboardingUpdate(0, {"priority_life_areas": ["health"]}), "request"
    )
    replay = service.update(
        account_id, OnboardingUpdate(0, {"priority_life_areas": ["health"]}), "request"
    )
    assert replay.version == state.version
    assert units[-1].commits == 0
    with pytest.raises(OnboardingStateConflictError):
        service.update(
            account_id,
            OnboardingUpdate(0, {"priority_life_areas": ["focus"]}),
            "request",
        )
    with pytest.raises(OnboardingIncompleteError):
        service.complete(account_id, 1, "request")
    with pytest.raises(OnboardingRequiredError):
        service.require_completed(account_id, "request")


def test_confirmation_and_budget_semantics():
    account_id, _, _, _, service = fixture_service()
    with pytest.raises(OnboardingUpdateRejectedError):
        service.update(
            account_id, OnboardingUpdate(0, {"unit_system_confirmed": True}), "request"
        )
    with pytest.raises(OnboardingUpdateRejectedError):
        service.update(
            account_id,
            OnboardingUpdate(
                0,
                {
                    "currency": "USD",
                    "currency_confirmed": True,
                    "goals": {"budget_target": {"amount": "12.00", "currency": "EUR"}},
                },
            ),
            "request",
        )


def test_optional_data_does_not_make_state_ready():
    account_id, _, _, _, service = fixture_service()
    state = service.update(
        account_id,
        OnboardingUpdate(
            0,
            {
                "integration_preferences": {"calendar": "deferred"},
                "notification_preferences": {"water": "enabled"},
            },
        ),
        "request",
    )
    assert state.status == "in_progress"
    assert not state.ready_to_complete


@pytest.mark.parametrize(
    "change",
    [
        {"priority_life_areas": ("unknown",)},
        {"unit_system": "unknown"},
        {"currency": "ZZZ"},
        {"unit_system": None, "unit_system_confirmed": True},
        {"unit_system_confirmed": "true"},
        {"currency_confirmed": 1},
        {"goals": []},
        {"goals": {"unknown": 1}},
        {"goals": {"daily_protein_grams": "NaN"}},
        {"goals": {"daily_protein_grams": "1E+1000000"}},
        {"integration_preferences": {"unknown": "deferred"}},
        {"notification_preferences": {"water": "granted"}},
    ],
)
def test_corrupt_persisted_state_is_rejected_without_value_disclosure(change):
    state = OnboardingState(
        user_account_id=uuid4(),
        version=1,
        created_at=NOW,
        updated_at=NOW,
        **change,
    )
    with pytest.raises(
        CorruptOnboardingStateError, match="Persisted onboarding state is invalid"
    ) as caught:
        validate_persisted_state(state)
    assert "unknown" not in str(caught.value)
    assert "NaN" not in str(caught.value)


@pytest.mark.parametrize(
    "change",
    [
        {"priority_life_areas": ("health", "health")},
        {"priority_life_areas": ("focus", "health")},
        {"contract_version": "2.0"},
        {"version": True},
        {"created_at": NOW.replace(tzinfo=None)},
        {"updated_at": NOW.replace(tzinfo=None)},
        {"completed_at": NOW.replace(tzinfo=None)},
        {"updated_at": NOW - timedelta(seconds=1)},
        {"completed_at": NOW - timedelta(seconds=1)},
        {"completed_at": NOW + timedelta(seconds=1)},
    ],
)
def test_corrupt_persisted_canonical_and_timestamp_state_is_rejected(change):
    values = {
        "user_account_id": uuid4(),
        "version": 1,
        "priority_life_areas": ("health",),
        "unit_system": "metric",
        "unit_system_confirmed": True,
        "currency": "USD",
        "currency_confirmed": True,
        "completed_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(change)
    with pytest.raises(CorruptOnboardingStateError):
        validate_persisted_state(OnboardingState(**values))


@pytest.mark.parametrize(
    "change",
    [
        {"priority_life_areas": ()},
        {"unit_system": None},
        {"unit_system_confirmed": False},
        {"currency": None},
        {"currency_confirmed": False},
    ],
)
def test_completed_persisted_state_requires_every_completion_fact(change):
    values = {
        "user_account_id": uuid4(),
        "version": 2,
        "priority_life_areas": ("health",),
        "unit_system": "metric",
        "unit_system_confirmed": True,
        "currency": "USD",
        "currency_confirmed": True,
        "completed_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(change)
    with pytest.raises(CorruptOnboardingStateError):
        validate_persisted_state(OnboardingState(**values))


def test_onboarding_rejects_unverified_disabled_and_cross_account_access():
    account_id, account, _, _, service = fixture_service()
    account.email_verified_at = None
    with pytest.raises(OnboardingEmailVerificationRequiredError):
        service.get(account_id, "unverified")
    account.email_verified_at = NOW
    account.status = AccountStatus.DISABLED
    with pytest.raises(AccountUnavailableError):
        service.get(account_id, "disabled")
    account.status = AccountStatus.ACTIVE
    other_account_id = uuid4()
    operations = (
        lambda: service.get(other_account_id, "cross-account-get"),
        lambda: service.update(
            other_account_id,
            OnboardingUpdate(0, {"priority_life_areas": ["health"]}),
            "cross-account-update",
        ),
        lambda: service.complete(other_account_id, 0, "cross-account-complete"),
    )
    for operation in operations:
        with pytest.raises(AccountUnavailableError):
            operation()


def test_all_optional_goals_validate_and_partial_updates_preserve_existing_goals():
    account_id, _, _, _, service = fixture_service()
    goals = {
        "target_weight": {"value": "72.125", "unit": "kg"},
        "daily_protein_grams": "120.5",
        "daily_water": {"value": "2.75", "unit": "liters"},
        "daily_calories_kcal": 2200,
        "daily_steps": 8000,
        "workouts_per_week": 4,
        "focus_minutes_per_day": 90,
        "target_sleep_minutes": 480,
        "budget_target": {"amount": "125.50", "currency": "USD"},
    }
    first = service.update(
        account_id,
        OnboardingUpdate(
            0,
            {"currency": "USD", "currency_confirmed": True, "goals": goals},
        ),
        "goals",
    )
    second = service.update(
        account_id,
        OnboardingUpdate(first.version, {"goals": {"daily_steps": 9000}}),
        "goals-update",
    )
    assert second.goals["daily_steps"] == 9000
    assert second.goals["target_weight"] == goals["target_weight"]


def test_preference_updates_preserve_authoritative_completion():
    account_id, _, _, _, service = fixture_service()
    ready = service.update(
        account_id,
        OnboardingUpdate(
            0,
            {
                "priority_life_areas": ["health"],
                "unit_system": "metric",
                "unit_system_confirmed": True,
                "currency": "USD",
                "currency_confirmed": True,
            },
        ),
        "ready",
    )
    completed = service.complete(account_id, ready.version, "complete")
    updated = service.update(
        account_id,
        OnboardingUpdate(
            completed.version,
            {"notification_preferences": {"water": "enabled"}},
        ),
        "preference",
    )
    assert updated.completed_at == completed.completed_at
    assert updated.is_complete


def test_completion_projection_failure_rolls_back_onboarding_mutation():
    account_id, account, states, _, _ = fixture_service()
    ready = OnboardingState(
        account_id,
        1,
        priority_life_areas=("health",),
        unit_system="metric",
        unit_system_confirmed=True,
        currency="USD",
        currency_confirmed=True,
        created_at=NOW,
        updated_at=NOW,
    )
    states[account_id] = ready

    class FailingAccounts(Accounts):
        def set_onboarding_required(self, account_id, required, updated_at):
            return False

    class TransactionalUnit(Unit):
        def __init__(self):
            super().__init__(states, account)
            self.accounts = FailingAccounts(account)
            self.before = dict(states)

        def __exit__(self, exception_type, *_args):
            if exception_type is not None:
                states.clear()
                states.update(self.before)

    service = OnboardingService(
        lambda _request_id: TransactionalUnit(), clock=lambda: NOW
    )
    with pytest.raises(OnboardingDependencyUnavailableError):
        service.complete(account_id, ready.version, "projection-failure")
    assert states[account_id] == ready
    assert account.onboarding_required is True


def test_idempotent_projection_repair_failure_is_safe_and_uncommitted():
    account_id, account, states, _, _ = fixture_service()
    completed = OnboardingState(
        account_id,
        2,
        priority_life_areas=("health",),
        unit_system="metric",
        unit_system_confirmed=True,
        currency="USD",
        currency_confirmed=True,
        completed_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    states[account_id] = completed

    class FailingAccounts(Accounts):
        def set_onboarding_required(self, account_id, required, updated_at):
            return False

    class RepairUnit(Unit):
        def __init__(self):
            super().__init__(states, account)
            self.accounts = FailingAccounts(account)

    service = OnboardingService(lambda _request_id: RepairUnit(), clock=lambda: NOW)
    with pytest.raises(OnboardingDependencyUnavailableError):
        service.complete(account_id, completed.version, "repair-failure")
    assert states[account_id] == completed
    assert account.onboarding_required is True
